"""
Analyzer multi-file backend directory whitelist tests.

The analyzer's `_MULTI_FILE_BACKEND_DIRS` decides which sub-directories under
`backend/` get walked when looking for Pydantic models and FastAPI routes.
This test pins the supported dir names so a future cleanup can't silently
drop one (e.g., removing `routers` would break every app that follows the
FastAPI tutorial's canonical convention).
"""
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ingestion.analyzer import EmergentAnalyzer


def _make_app(tmp_path: Path, sub_dir: str) -> Path:
    """Build a minimal Emergent-shaped app with a single router file at
    `backend/<sub_dir>/users.py`."""
    backend = tmp_path / "backend"
    (backend / sub_dir).mkdir(parents=True)
    (backend / "server.py").write_text(textwrap.dedent("""
        from fastapi import FastAPI
        app = FastAPI()
    """).strip())
    (backend / sub_dir / "users.py").write_text(textwrap.dedent("""
        from fastapi import APIRouter
        from pydantic import BaseModel

        router = APIRouter()

        class User(BaseModel):
            user_id: str
            email: str

        @router.get("/users")
        async def list_users():
            return []

        @router.post("/users")
        async def create_user(payload: User):
            return payload
    """).strip())
    return tmp_path


@pytest.mark.parametrize("sub_dir", [
    "routes",
    "routers",
    "api",
    "app/routes",
    "app/routers",
    "src/routes",
    "src/routers",
])
def test_multi_file_dir_picked_up(tmp_path, sub_dir):
    """The analyzer must walk every dir in `_MULTI_FILE_BACKEND_DIRS`.

    Regression target: `routers` was missing pre-patch, which made every
    FastAPI-tutorial-shaped app (including MATCH) surface 0 routes during
    Phase 1 analysis.
    """
    app_dir = _make_app(tmp_path, sub_dir)
    a = EmergentAnalyzer(str(app_dir))
    a.analyze()
    assert len(a.routes) == 2, (
        f"Expected 2 routes from a single users.py under backend/{sub_dir}/, "
        f"got {len(a.routes)}. Analyzer likely doesn't walk that sub-dir."
    )
    assert "User" in a.models, (
        f"Expected the User Pydantic model to be detected under "
        f"backend/{sub_dir}/users.py, got: {list(a.models.keys())}"
    )


def test_root_level_models_py_is_scanned(tmp_path):
    """`backend/models.py` (a single file sibling of `server.py`) must be parsed.

    Regression target: MATCH puts every domain model — Session, ConnectionConfig,
    Row payload shapes — in `backend/models.py`. Before this patch the analyzer
    only walked named sub-directories and missed all of them.
    """
    backend = tmp_path / "backend"
    backend.mkdir(parents=True)
    (backend / "server.py").write_text(textwrap.dedent("""
        from fastapi import FastAPI
        app = FastAPI()
    """).strip())
    (backend / "models.py").write_text(textwrap.dedent("""
        from pydantic import BaseModel

        class Session(BaseModel):
            session_id: str
            status: str

        class SessionCreate(BaseModel):
            name: str
    """).strip())

    a = EmergentAnalyzer(str(tmp_path))
    a.analyze()
    assert "Session" in a.models, (
        f"Expected Session model from backend/models.py to be detected; "
        f"got: {list(a.models.keys())}"
    )
    assert "SessionCreate" in a.models, (
        f"Expected SessionCreate model from backend/models.py to be detected; "
        f"got: {list(a.models.keys())}"
    )


def test_router_prefix_is_prepended_to_route_paths(tmp_path):
    """`APIRouter(prefix="/api/sessions")` declared at the top of a router file
    must be prepended to each route's path. Without this, entity-route binding
    in `_infer_entities` cannot match `/api/sessions/{id}` against `session`.

    Regression target: MATCH defines its routers with prefixes in the
    `APIRouter(...)` call, not on `include_router(...)`. Pre-patch, the path
    `@router.get("/{session_id}")` ended up as `/{session_id}` in `self.routes`,
    silently breaking every downstream entity-route match.
    """
    backend = tmp_path / "backend"
    (backend / "routers").mkdir(parents=True)
    (backend / "server.py").write_text(textwrap.dedent("""
        from fastapi import FastAPI
        app = FastAPI()
    """).strip())
    (backend / "routers" / "sessions.py").write_text(textwrap.dedent("""
        from fastapi import APIRouter
        from pydantic import BaseModel

        router = APIRouter(prefix="/api/sessions", tags=["sessions"])

        class Session(BaseModel):
            session_id: str

        @router.get("/{session_id}")
        async def get_session(session_id: str):
            return {"session_id": session_id}

        @router.post("")
        async def create_session():
            return {}
    """).strip())

    a = EmergentAnalyzer(str(tmp_path))
    a.analyze()
    paths = sorted(r["path"] for r in a.routes)
    assert paths == ["/api/sessions", "/api/sessions/{session_id}"], (
        f"Expected router prefix `/api/sessions` to be prepended to each route, "
        f"got: {paths}"
    )


def test_entity_route_matches_hyphenated_url(tmp_path):
    """Entity-route binding must match hyphenated URL slugs against underscored
    entity names. Routes mounted at `/api/provider-templates` must bind to the
    `provider_template` entity for downstream extract/register to find them.
    """
    backend = tmp_path / "backend"
    (backend / "routers").mkdir(parents=True)
    (backend / "server.py").write_text(textwrap.dedent("""
        from fastapi import FastAPI
        app = FastAPI()
    """).strip())
    (backend / "routers" / "provider_templates.py").write_text(textwrap.dedent("""
        from fastapi import APIRouter
        from pydantic import BaseModel

        router = APIRouter(prefix="/api/provider-templates", tags=["templates"])

        class ProviderTemplate(BaseModel):
            template_id: str
            name: str

        @router.get("")
        async def list_templates():
            return []

        @router.get("/{template_id}")
        async def get_template(template_id: str):
            return {}
    """).strip())

    a = EmergentAnalyzer(str(tmp_path))
    a.analyze()
    ent = a.entities.get("provider_template")
    assert ent is not None, (
        f"Expected provider_template entity to be inferred; entities: {list(a.entities)}"
    )
    assert len(ent["routes"]) == 2, (
        f"Expected provider_template entity to bind 2 routes from "
        f"/api/provider-templates/*; got {len(ent['routes'])}. Hyphen-aware "
        f"matcher likely broken."
    )


def test_app_decorator_paths_are_not_prefixed(tmp_path):
    """`@app.get("/api/health")` in server.py must keep its literal path — the
    router-prefix logic must only apply to `@<router>.<method>` decorators.
    """
    backend = tmp_path / "backend"
    backend.mkdir(parents=True)
    (backend / "server.py").write_text(textwrap.dedent("""
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/api/health")
        async def health():
            return {"ok": True}
    """).strip())

    a = EmergentAnalyzer(str(tmp_path))
    a.analyze()
    assert any(r["path"] == "/api/health" for r in a.routes), (
        f"Expected /api/health from @app.get to be kept verbatim; got: "
        f"{[r['path'] for r in a.routes]}"
    )
