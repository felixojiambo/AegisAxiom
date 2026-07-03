"""
Tests for mapping file generation — service layer and API endpoints.

Service tests mock repo.get_mapping_data / repo.log_stage and monkeypatch
MAPPINGS_DIR so no real filesystem or database is required.

Route tests import endpoint functions directly (bypassing auth middleware) and
patch has_perm + service/repo callables. auth_helper raises if JWT_SECRET is
unset, so we set a dummy value in the environment before importing routes.

Thread 2E owns this file. Thread 2D owns the service implementation.
All mocks are written against the agreed interface contract.
"""
import json
import os
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Make auth_helper importable in a test environment — it raises at module level
# if JWT_SECRET is absent. Set a throwaway value before any route import.
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-only-not-production")


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

APP_ID = "app_davies_001"
APP_SLUG = "davies_pmo"


def _make_app_doc(app_id=APP_ID, app_slug=APP_SLUG):
    return {
        "app_id": app_id,
        "app_slug": app_slug,
        "name": "Davies PMO",
        "slug": "davies-pmo",
        "status": "active",
        "source_commit_sha": "abc123def456",
        "ingestion_version": 1,
    }


def _make_catalogue(entity_name="project", has_status=True):
    """Build a minimal catalogue dict matching what repo.get_mapping_data returns.

    Key detail from 2D's implementation:
    - fields use "type" (not "field_type")
    - enums link to a field via "field_name" (not by matching enum name)
    """
    fields = [
        {"name": "title", "type": "string", "source_field_name": "title"},
        {"name": "owner_id", "type": "string", "source_field_name": "ownerId"},
    ]
    enums = []
    if has_status:
        fields.append({"name": "status", "type": "string", "source_field_name": "status"})
        enums = [{"field_name": "status", "values": ["draft", "active", "archived"]}]
    return {
        "entity_name": entity_name,
        "fields": fields,
        "enums": enums,
        "source_collection": f"{entity_name}s",
        "id_field": "_id",
    }


def _make_mapping_data(entity_name="project", has_status=True):
    return {
        "app": _make_app_doc(),
        "catalogues": [_make_catalogue(entity_name=entity_name, has_status=has_status)],
        "modules": [{"module_name": f"{APP_SLUG}_{entity_name}s"}],
    }


# ---------------------------------------------------------------------------
# Patch targets for service-layer tests
# ---------------------------------------------------------------------------

PATCH_REPO_MAPPING = "services.ingestion.service.repo.get_mapping_data"
PATCH_REPO_LOG = "services.ingestion.service.repo.log_stage"


# ---------------------------------------------------------------------------
# Service layer tests — 9 tests
# ---------------------------------------------------------------------------

class TestGenerateMappingFileService:

    @pytest.mark.asyncio
    async def test_generate_mapping_file_success(self, tmp_path):
        """Returned dict has required top-level and entity-level keys."""
        from services.ingestion import service

        mapping_data = _make_mapping_data()
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        assert "app_slug" in result
        assert "source_commit_sha" in result
        assert "ingestion_version" in result
        assert "entities" in result
        # generated_at deliberately NOT in the contract dict — see sidecar test below
        assert "generated_at" not in result

        entity = result["entities"].get("project")
        assert entity is not None, "Expected 'project' entity in output"
        for key in ("collection", "id_field", "fields", "status_enum", "events"):
            assert key in entity, f"Missing key '{key}' in entity dict"

    @pytest.mark.asyncio
    async def test_generate_mapping_file_creates_directory(self, tmp_path):
        """MAPPINGS_DIR is created if it doesn't already exist."""
        from services.ingestion import service

        target_dir = tmp_path / "new_mappings"
        assert not target_dir.exists()

        mapping_data = _make_mapping_data()
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", target_dir):
            await service.generate_mapping_file(APP_ID)

        assert target_dir.exists()

    @pytest.mark.asyncio
    async def test_generate_mapping_file_writes_json(self, tmp_path):
        """File written at MAPPINGS_DIR/{app_slug}.json and contains valid JSON."""
        from services.ingestion import service

        mapping_data = _make_mapping_data()
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        mapping_file = tmp_path / f"{APP_SLUG}.json"
        assert mapping_file.exists(), "Mapping file was not written to disk"
        on_disk = json.loads(mapping_file.read_text(encoding="utf-8"))
        assert on_disk == result

    @pytest.mark.asyncio
    async def test_generate_mapping_file_app_not_found(self, tmp_path):
        """ValueError raised (with app_id in message) when get_mapping_data returns None."""
        from services.ingestion import service

        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=None)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            with pytest.raises(ValueError, match=APP_ID):
                await service.generate_mapping_file(APP_ID)

    @pytest.mark.asyncio
    async def test_generate_mapping_file_status_enum_extracted(self, tmp_path):
        """status_enum is populated from the enum whose field_name matches 'status'."""
        from services.ingestion import service

        mapping_data = _make_mapping_data(has_status=True)
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        status_enum = result["entities"]["project"]["status_enum"]
        assert status_enum == ["draft", "active", "archived"]

    @pytest.mark.asyncio
    async def test_generate_mapping_file_no_status_field(self, tmp_path):
        """No status field → status_enum is [] and status_changed event absent."""
        from services.ingestion import service

        mapping_data = _make_mapping_data(has_status=False)
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        entity = result["entities"]["project"]
        assert entity["status_enum"] == []
        assert "project_status_changed" not in entity["events"]

    @pytest.mark.asyncio
    async def test_generate_mapping_file_status_changed_event_added(self, tmp_path):
        """Entity WITH a status field gets {entity}_status_changed in events."""
        from services.ingestion import service

        mapping_data = _make_mapping_data(has_status=True)
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        assert "project_status_changed" in result["entities"]["project"]["events"]

    @pytest.mark.asyncio
    async def test_generate_mapping_file_field_source_name_fallback(self, tmp_path):
        """Field with source_field_name=None uses the field's own name as source_name."""
        from services.ingestion import service

        mapping_data = _make_mapping_data()
        mapping_data["catalogues"][0]["fields"].append(
            {"name": "notes", "type": "string", "source_field_name": None}
        )
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        fields_out = result["entities"]["project"]["fields"]
        # 2D outputs fields as a dict {name: {type, source_name}}
        assert "notes" in fields_out, "notes field missing from output"
        assert fields_out["notes"]["source_name"] == "notes"

    @pytest.mark.asyncio
    async def test_generate_mapping_file_collection_naming(self, tmp_path):
        """Collection name follows {app_slug}_{pluralized_entity_name} convention."""
        from services.ingestion import service

        mapping_data = _make_mapping_data(entity_name="project")
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            result = await service.generate_mapping_file(APP_ID)

        assert result["entities"]["project"]["collection"] == f"{APP_SLUG}_projects"
    @pytest.mark.asyncio
    async def test_generate_mapping_file_is_byte_identical_across_runs(self, tmp_path):
        """Same source data → byte-identical mapping file on repeated runs.

        /reingest-app diffs this file to detect what changed since the last
        ingestion, so a moving timestamp or other run-specific data inside the
        contract would always register a delta. Run-specific data lives in the
        .meta.json sidecar instead — verified separately."""
        from services.ingestion import service

        mapping_data = _make_mapping_data()
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            await service.generate_mapping_file(APP_ID)
            first_bytes = (tmp_path / f"{APP_SLUG}.json").read_bytes()

            await service.generate_mapping_file(APP_ID)
            second_bytes = (tmp_path / f"{APP_SLUG}.json").read_bytes()

        assert first_bytes == second_bytes, (
            "Mapping file must be byte-identical across runs to keep the "
            "contract content-addressable. Run-specific data belongs in the "
            ".meta.json sidecar."
        )

    @pytest.mark.asyncio
    async def test_generate_mapping_file_writes_meta_sidecar(self, tmp_path):
        """The .meta.json sidecar carries the generated_at timestamp."""
        from services.ingestion import service

        mapping_data = _make_mapping_data()
        with patch(PATCH_REPO_MAPPING, AsyncMock(return_value=mapping_data)), \
             patch(PATCH_REPO_LOG, AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            await service.generate_mapping_file(APP_ID)

        meta_path = tmp_path / f"{APP_SLUG}.meta.json"
        assert meta_path.exists(), "Expected .meta.json sidecar"
        meta = json.loads(meta_path.read_text())
        assert "generated_at" in meta
        assert meta["app_slug"] == APP_SLUG


class TestRegisterFieldsAutoTriggerIsolation:
    """The auto-trigger from register_fields_from_catalogue must not propagate
    mapping-generation failures. Registration is the source of truth."""

    @pytest.mark.asyncio
    async def test_register_survives_mapping_failure(self):
        """If generate_mapping_file raises, register_fields_from_catalogue
        still completes successfully and logs a warning."""
        from services.ingestion import service

        # Patch generate_mapping_file at the service module level so any internal
        # call inside register_fields_from_catalogue routes to the failing mock.
        # We don't care about the rest of the registration logic for this test —
        # we only need to verify that an exception in mapping generation does
        # not bubble out. Patch the boundary functions registration touches.
        with patch.object(
            service, "generate_mapping_file",
            AsyncMock(side_effect=RuntimeError("simulated mapping failure")),
        ), patch.object(
            service.repo, "get_app_registry",
            AsyncMock(return_value={
                "app_id": APP_ID,
                "slug": APP_SLUG,
                "app_slug": APP_SLUG,
                "status": "extracting",
            }),
        ), patch.object(
            service.repo, "update_app_status", AsyncMock(),
        ), patch.object(
            service.repo, "get_field_catalogues",
            AsyncMock(return_value=[]),
        ), patch.object(
            service.repo, "log_stage", AsyncMock(),
        ), patch.object(
            service.repo, "write_log", AsyncMock(),
        ), patch.object(
            service.repo, "update_stage", AsyncMock(),
        ), patch(
            "services.ingestion.service.get_db",
            return_value=MagicMock(
                policy_targets=MagicMock(
                    update_one=AsyncMock(),
                    update_many=AsyncMock(return_value=MagicMock(modified_count=0)),
                ),
            ),
        ):
            # Should NOT raise — the failure is logged and swallowed.
            try:
                result = await service.register_fields_from_catalogue(APP_ID)
            except RuntimeError as exc:
                pytest.fail(
                    f"register_fields_from_catalogue propagated mapping failure: {exc}"
                )

        # Sanity check: the registration completed.
        assert result is not None


# ---------------------------------------------------------------------------
# Route / endpoint tests — 3 tests
# Imports are deferred inside each test so the JWT_SECRET env var is already
# set before auth_helper loads.
# ---------------------------------------------------------------------------

def _make_user(has_admin=True):
    u = SimpleNamespace()
    u.user_id = "user_admin_001"
    u.email = "admin@example.com"
    return u


class TestMappingEndpoints:

    @pytest.mark.asyncio
    async def test_post_mapping_endpoint_requires_auth(self):
        """POST /ingestions/{app_id}/mapping → 403 when user lacks admin.manage."""
        from routes.app_ingestion import generate_mapping_endpoint

        non_admin = _make_user()
        with patch("routes.app_ingestion.has_perm", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await generate_mapping_endpoint(app_id=APP_ID, current_user=non_admin)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_post_mapping_endpoint_app_not_found(self):
        """POST /ingestions/{app_id}/mapping → 404 when service raises ValueError."""
        from routes.app_ingestion import generate_mapping_endpoint

        admin = _make_user()
        with patch("routes.app_ingestion.has_perm", return_value=True), \
             patch("routes.app_ingestion.service.generate_mapping_file",
                   AsyncMock(side_effect=ValueError(f"App {APP_ID} not found"))):
            with pytest.raises(HTTPException) as exc_info:
                await generate_mapping_endpoint(app_id=APP_ID, current_user=admin)

        assert exc_info.value.status_code == 404
        assert APP_ID in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_mapping_endpoint_returns_404_when_no_file(self, tmp_path):
        """GET /ingestions/{app_id}/mapping → 404 with correct message when file absent."""
        from routes.app_ingestion import get_mapping_endpoint
        from services.ingestion import service

        admin = _make_user()
        app_doc = _make_app_doc()

        # tmp_path exists but contains no mapping file, so the exists() check fails.
        with patch("routes.app_ingestion.has_perm", return_value=True), \
             patch("routes.app_ingestion.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await get_mapping_endpoint(app_id=APP_ID, current_user=admin)

        assert exc_info.value.status_code == 404
        assert "not generated yet" in exc_info.value.detail
