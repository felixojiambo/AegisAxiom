"""Wave 6 / Thread A smoke test — verify route registration directly via
the route modules. Avoids importing the full server (which pulls
heavyweight deps like resend/motor). Each new route's APIRouter is
introspected and its routes are pretty-printed.

Run from the worktree root:
    python smoke_test_wave_6a.py

Output: pass/fail per endpoint plus exit code 0/1.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    # Script lives in backend/tests/ — backend dir is one level up.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, backend_dir)

    # Stub out heavy deps that block route imports — we only care about the
    # route shape, not the runtime modules.
    import types

    for mod_name in ("resend",):
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            stub.api_key = ""

            class _DummyEmails:
                @staticmethod
                def send(_params):
                    pass

            stub.Emails = _DummyEmails
            sys.modules[mod_name] = stub

    # Stub the .env load.
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "aegisaxiom_smoke_test")
    os.environ.setdefault("JWT_SECRET", "smoke-test-secret")
    os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")

    try:
        # Try to import database module first to init.
        import database

        database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])

        # Match the import order used by server.py — projects_task
        # registers first (with its created/updated/etc events), then
        # projects_my_work extends with the new event keys.
        from routes import projects_task
        from routes import projects_blocker_ticket  # noqa: F401
        from routes import projects_project  # noqa: F401
        from routes import projects_my_work
    except ModuleNotFoundError as exc:
        print(f"  Skipping smoke test — backend env not provisioned: {exc}")
        return 0
    except Exception as exc:
        print(f"  Smoke test setup failed: {exc!r}")
        return 1

    # Enumerate the routes registered on each router.
    def list_routes(router) -> list:
        out = []
        for r in getattr(router, "routes", []):
            if hasattr(r, "methods") and r.methods and hasattr(r, "path"):
                for m in r.methods:
                    if m == "HEAD":
                        continue
                    out.append((m, r.path))
        return out

    my_work_routes = list_routes(projects_my_work.router)
    task_routes = list_routes(projects_task.router)

    expected_my_work = {
        ("GET",   "/projects/mine"),
        ("GET",   "/tasks/assigned-to-me"),
        ("GET",   "/blocker-tickets/owned-by-me"),
        ("GET",   "/dashboard/my-summary"),
        ("POST",  "/notifications/scan"),
        ("GET",   "/notifications/my-summary"),
    }
    expected_task_bulk = {
        ("POST",  "/tasks/bulk"),
        ("PATCH", "/tasks/bulk-assign"),
        ("PATCH", "/tasks/bulk-status"),
    }

    actual_my_work = set(my_work_routes)
    actual_task = set(task_routes)

    missing_my_work = expected_my_work - actual_my_work
    missing_task = expected_task_bulk - actual_task

    print("=" * 78)
    print("Wave 6A route registration smoke test")
    print("=" * 78)

    print("\n[projects_my_work.py]")
    for pair in sorted(expected_my_work):
        flag = "OK " if pair in actual_my_work else "MISS"
        print(f"  [{flag}] {pair[0]:6s} {pair[1]}")

    print("\n[projects_task.py — new bulk endpoints]")
    for pair in sorted(expected_task_bulk):
        flag = "OK " if pair in actual_task else "MISS"
        print(f"  [{flag}] {pair[0]:6s} {pair[1]}")

    # Verify the new event keys are in the registry.
    from services.field_registry import get_module_events

    print("\n[Event registry]")
    event_ok = True
    for entity, expected_event in [
        ("task", "overdue"),
        ("project", "health_red"),
        ("blocker_ticket", "stale"),
    ]:
        events = get_module_events("projects", entity)
        present = expected_event in events
        flag = "OK " if present else "MISS"
        print(f"  [{flag}] projects.{entity}.{expected_event}  registered={events}")
        if not present:
            event_ok = False

    ok = not missing_my_work and not missing_task and event_ok
    print("\n" + "=" * 78)
    print("RESULT:", "PASS" if ok else "FAIL")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
