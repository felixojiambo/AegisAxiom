"""
Tests for contract validation — service.validate_against_mapping.

Validation scans generated code files against the mapping contract written by
generate_mapping_file (Wave 2). It returns a structured report of mismatches
between the generated code and the mapping, without auto-fixing anything.

Tests use tmp_path for filesystem fixtures and patch
service.MAPPINGS_DIR / repo.get_app_registry. JWT_SECRET is set before any
module that touches auth_helper imports, mirroring test_mapping_generation.py.

Thread 3F owns this file. Thread 2D delivered generate_mapping_file in Wave 2;
this module exercises the new validate_against_mapping function added in 3F.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# auth_helper raises at import time if JWT_SECRET is missing — set it before
# any route module is imported transitively.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-key-for-validation-only-32+chars"
)


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


def _write_mapping(tmp_path: Path, app_slug: str = APP_SLUG, **overrides) -> Path:
    """Write a default mapping file with sensible per-test overrides."""
    mapping = {
        "app_slug": app_slug,
        "source_commit_sha": "abc123",
        "ingestion_version": 1,
        "entities": {
            "customer": {
                "collection": f"{app_slug}_customers",
                "id_field": "customer_id",
                "fields": {
                    "customer_id": {"type": "string", "source_name": "customerId"},
                    "customer_name": {"type": "string", "source_name": "customerName"},
                    "email": {"type": "string", "source_name": "email"},
                    "status": {"type": "string", "source_name": "status"},
                },
                "status_enum": ["draft", "active"],
                "events": [
                    "customer_created", "customer_updated",
                    "customer_deleted", "customer_status_changed",
                ],
            },
            "project": {
                "collection": f"{app_slug}_projects",
                "id_field": "project_id",
                "fields": {
                    "project_id": {"type": "string", "source_name": "projectId"},
                    "title": {"type": "string", "source_name": "title"},
                    "status": {"type": "string", "source_name": "status"},
                },
                "status_enum": ["draft", "active", "archived"],
                "events": [
                    "project_created", "project_updated",
                    "project_deleted", "project_status_changed",
                ],
            },
        },
    }
    if overrides:
        # Shallow merge for convenience.
        mapping.update(overrides)
    path = tmp_path / f"{app_slug}.json"
    path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return path


PATCH_GET_APP = "services.ingestion.service.repo.get_app_registry"


class TestValidateAgainstMapping:

    @pytest.mark.asyncio
    async def test_validate_with_matching_fields(self, tmp_path):
        """Generated file references known fields → valid=True, no issues."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        gen_file = tmp_path / "customer_routes.py"
        gen_file.write_text(
            "def get_customer(customer):\n"
            "    name = customer[\"customer_name\"]\n"
            "    return name\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        assert report["valid"] is True
        assert report["checked_files"] == 1
        assert report["summary"] == {"errors": 0, "warnings": 0}
        assert report["issues"] == []

    @pytest.mark.asyncio
    async def test_validate_detects_unknown_field(self, tmp_path):
        """Generated file uses 'customer' instead of 'customer_name' → error."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        gen_file = tmp_path / "customer_routes.py"
        # 'customer' is NOT one of the mapping fields — only customer_name is.
        gen_file.write_text(
            "def make(customer):\n"
            "    return customer[\"customer\"]\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        assert report["valid"] is False
        assert report["summary"]["errors"] >= 1
        # The bracket-style field ref should be flagged.
        assert any(
            i["severity"] == "error" and "customer" in i["issue"]
            for i in report["issues"]
        )

    @pytest.mark.asyncio
    async def test_validate_detects_enum_mismatch(self, tmp_path):
        """status === "archived" not in customer enum → flagged."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        # Frontend file under a path that lets the heuristic infer 'customer'.
        gen_file = tmp_path / "customer_panel.jsx"
        gen_file.write_text(
            "function CustomerPanel({ customer }) {\n"
            "  if (status === \"archived\") {\n"
            "    return null;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        assert report["valid"] is False
        assert any(
            i["severity"] in {"error", "warning"} and "archived" in i["issue"]
            for i in report["issues"]
        )

    @pytest.mark.asyncio
    async def test_validate_detects_id_field_mismatch(self, tmp_path):
        """Frontend `project.id` should warn — mapping uses project_id."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        gen_file = tmp_path / "project_card.jsx"
        gen_file.write_text(
            "function ProjectCard({ project }) {\n"
            "  return <div>{project.id}</div>;\n"
            "}\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        assert any(
            i["severity"] == "warning" and "project_id" in i["issue"]
            for i in report["issues"]
        ), f"Expected an id-field warning, got: {report['issues']}"

    @pytest.mark.asyncio
    async def test_validate_no_mapping_file(self, tmp_path):
        """No mapping file on disk → valid=False, single error issue, no raise."""
        from services.ingestion import service

        # Note: NOT writing any mapping file.
        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(APP_ID)

        assert report["valid"] is False
        assert report["checked_files"] == 0
        assert report["summary"]["errors"] == 1
        assert len(report["issues"]) == 1
        assert report["issues"][0]["severity"] == "error"
        assert "not found" in report["issues"][0]["issue"].lower()

    @pytest.mark.asyncio
    async def test_validate_app_not_found(self, tmp_path):
        """get_app_registry returns None → ValueError raised with app_id."""
        from services.ingestion import service

        with patch(PATCH_GET_APP, AsyncMock(return_value=None)), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            with pytest.raises(ValueError, match=APP_ID):
                await service.validate_against_mapping(APP_ID)

    @pytest.mark.asyncio
    async def test_validate_empty_generated_files(self, tmp_path):
        """Explicit empty list → valid=True, checked_files=0, no issues."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[]
            )

        assert report["valid"] is True
        assert report["checked_files"] == 0
        assert report["issues"] == []
        assert report["summary"] == {"errors": 0, "warnings": 0}

    @pytest.mark.asyncio
    async def test_validate_skips_test_files(self, tmp_path):
        """File whose name starts with test_ is skipped, even with bad refs."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        # Filename triggers the test-file heuristic.
        gen_file = tmp_path / "test_customer_routes.py"
        gen_file.write_text(
            "def test_thing(customer):\n"
            "    return customer[\"totally_unknown_field\"]\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        # Skipped → not counted as checked, no issues raised.
        assert report["checked_files"] == 0
        assert report["issues"] == []
        assert report["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_summary_counts_correct(self, tmp_path):
        """Multiple issues across files → summary counts errors and warnings."""
        from services.ingestion import service

        _write_mapping(tmp_path)

        # File A: an unknown bracket-style field reference (error).
        bad_backend = tmp_path / "customer_routes.py"
        bad_backend.write_text(
            "def go(customer):\n"
            "    return customer[\"not_a_field\"]\n",
            encoding="utf-8",
        )

        # File B: a frontend `.id` reference (warning) AND a bad enum (error).
        bad_frontend = tmp_path / "project_card.jsx"
        bad_frontend.write_text(
            "function ProjectCard({ project }) {\n"
            "  if (status === \"nonsense\") return null;\n"
            "  return <div>{project.id}</div>;\n"
            "}\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID,
                generated_files=[str(bad_backend), str(bad_frontend)],
            )

        # Tallies match the issue list.
        errors = sum(1 for i in report["issues"] if i["severity"] == "error")
        warnings = sum(1 for i in report["issues"] if i["severity"] == "warning")
        assert report["summary"] == {"errors": errors, "warnings": warnings}
        assert report["summary"]["errors"] >= 1
        assert report["summary"]["warnings"] >= 1
        assert report["valid"] is False
        assert report["checked_files"] == 2

    @pytest.mark.asyncio
    async def test_validate_detects_wrong_collection_name(self, tmp_path):
        """db['davies_pmo_customer'] (singular) → flagged; mapping has plural."""
        from services.ingestion import service

        _write_mapping(tmp_path)
        gen_file = tmp_path / "customer_routes.py"
        gen_file.write_text(
            "async def list_customers():\n"
            "    return await db[\"davies_pmo_customer\"].find().to_list(100)\n",
            encoding="utf-8",
        )

        with patch(PATCH_GET_APP, AsyncMock(return_value=_make_app_doc())), \
                patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await service.validate_against_mapping(
                APP_ID, generated_files=[str(gen_file)]
            )

        assert report["valid"] is False
        assert any(
            i["severity"] == "error" and "davies_pmo_customer" in i["issue"]
            for i in report["issues"]
        ), f"Expected collection-name error, got: {report['issues']}"
