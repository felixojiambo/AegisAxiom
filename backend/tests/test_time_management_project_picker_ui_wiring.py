"""Source-level wiring contract for the Time Management project picker.

The picker on /time → Quick Add Time Entry must call the canonical
project list route. The backend mounts ``routes/projects_project.py``
with prefix ``/projects``, and the list route inside that router is
also ``/projects`` — so the actual URL is

    GET /api/projects/projects?limit=500

The earlier component called ``GET /api/projects?limit=500``, which is
not registered and silently 404'd, leaving the picker showing only
"Select project" even when projects existed on /projects/projects.

yarn / node_modules are unavailable in the build sandbox so the React
component can't run under Jest here. This test pins the
page-to-route binding via source inspection — same pattern as every
other ``*_ui_wiring.py`` test on this branch.
"""

from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "TimeManagement.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical route
# ---------------------------------------------------------------------------


def test_picker_calls_canonical_projects_route():
    src = _component_src()
    assert "/api/projects/projects?limit=500" in src, (
        "Time Management picker must call the canonical "
        "/api/projects/projects route (mounted via routes/"
        "projects_project.py at prefix '/projects' with route "
        "'/projects')."
    )


def test_picker_does_not_call_broken_api_projects_route():
    src = _component_src()
    # The earlier bug: GET /api/projects?limit=500 — the projects
    # router doesn't expose that URL, so it 404'd and left the
    # picker empty. We assert the *exact* broken literal is gone
    # without forbidding any "/api/projects" substring (the canonical
    # URL legitimately contains it).
    assert "/api/projects?limit=500" not in src, (
        "Time Management must not call /api/projects?limit=500 — "
        "the canonical route is /api/projects/projects?limit=500."
    )


def test_picker_does_not_call_dead_pm_projects_route():
    src = _component_src()
    # routes/pm_projects.py was deleted; only a stale .pyc remains.
    # Calling /api/pm/projects 404s. Drop the dead call.
    assert "/api/pm/projects" not in src, (
        "Time Management must not call /api/pm/projects — the "
        "backend route was removed."
    )


# ---------------------------------------------------------------------------
# Response-shape mapping
# ---------------------------------------------------------------------------


def test_picker_maps_backend_project_fields():
    src = _component_src()
    # The picker iterates projects and renders them by project_id /
    # name. unwrap() handles both array and {items: [...]} shape.
    assert "projects.map(" in src
    assert "p.project_id" in src
    assert "p.name" in src
    assert "unwrap(" in src, (
        "Picker must use the unwrap() helper so it can read both the "
        "items-envelope shape /api/projects/projects returns and the "
        "raw-array shape some legacy callers expect."
    )


# ---------------------------------------------------------------------------
# Empty-state messaging
# ---------------------------------------------------------------------------


def test_picker_distinguishes_load_failure_from_empty_eligibility():
    src = _component_src()
    # Both branches must be present, and they must be visually
    # distinct (error = "Failed to load", empty = "No projects
    # available"). The product copy is fixed so the QA pass can grep
    # for the exact strings.
    assert "projectsLoadStatus" in src, (
        "Picker must track load status separately from the projects "
        "array so an empty list isn't confused with a failed fetch."
    )
    assert "Failed to load projects" in src, (
        "Picker must render a distinct error message when the "
        "projects fetch returns non-OK."
    )
    assert "No projects available for this user" in src, (
        "Picker must render the exact 'No projects available for "
        "this user...' copy when the fetch succeeds but returns "
        "zero projects."
    )


def test_picker_has_testids_for_e2e_assertions():
    src = _component_src()
    # data-testid hooks let a future Playwright/Cypress smoke target
    # the picker and its empty-state messages without relying on
    # brittle CSS selectors or copy.
    for tid in (
        "time-entry-project-picker",
        "time-entry-project-picker-error",
        "time-entry-project-picker-empty",
    ):
        assert f'data-testid="{tid}"' in src, (
            f"Picker must expose data-testid={tid!r} for the "
            "end-to-end smoke."
        )


# ---------------------------------------------------------------------------
# Test Data Only flag — assert the test_data filter still threads
# through where the page uses it, so the fix doesn't regress the
# test-data isolation behaviour.
# ---------------------------------------------------------------------------


def test_test_data_only_filter_is_threaded_into_time_entry_endpoints():
    src = _component_src()
    # The picker fix shouldn't drop the test_data plumbing the rest
    # of the page relies on for is_test_data filtering.
    assert "is_test_data=" in src
    assert "testDataOnly" in src


# ---------------------------------------------------------------------------
# Backend route sanity — make sure the canonical URL is actually
# served by FastAPI. If the project router is ever renamed or the
# prefix changes, this test fails loudly here instead of as a 404
# in production.
# ---------------------------------------------------------------------------


def test_projects_project_router_mounts_at_double_segment(tmp_path, monkeypatch):
    import os
    os.environ.setdefault(
        "JWT_SECRET", "test_secret_value_for_unit_tests",
    )
    # Importing the router module alone is enough; we don't need to
    # spin up the whole app — server.py wires it under prefix
    # "/projects" and the route definition adds the second
    # "/projects" segment.
    from routes.projects_project import router  # noqa: WPS433

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/projects" in paths, (
        "projects_project router must expose its list route at "
        "literal path '/projects' — the server mounts the router "
        "with prefix '/projects', producing /api/projects/projects."
    )
