"""
Tests for the reconciliation service (services/ingestion/reconcile.py)
and its API endpoints in routes/app_ingestion.py.

reconcile_app() reads:
  - field_registry (in-memory dicts populated at import time)
  - repo.get_app_registry / repo.get_field_catalogues
  - mongo collections .permissions and .policy_targets
  - the on-disk mapping file at MAPPINGS_DIR/{app_slug}.json

It writes:
  - permission_registry.register_permissions(source="reconcile_auto")
  - service._register_policy_targets(...)
  - service.generate_mapping_file(...) (refresh)
  - repo.write_log + repo.log_stage (audit trail)

We mock all of those boundaries so the tests are pure-Python and need
no MongoDB / filesystem state.
"""
from __future__ import annotations

import os
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

# auth_helper raises at import time when JWT_SECRET is unset.
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-only-not-production")


APP_ID = "app_widgets_001"
APP_SLUG = "widgets"
APP_NAME = "Widgets PMO"


# ---------------------------------------------------------------------------
# Helpers — async cursor + db mocks
# ---------------------------------------------------------------------------


def _make_async_cursor(docs):
    """Return an async-iterable cursor mock that yields each doc."""

    class AsyncCursor:
        def __init__(self, items):
            self._iter = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    return AsyncCursor(docs)


def _make_db(persisted_perm_keys=None, persisted_targets=None):
    """Build a mock db with permissions.find / policy_targets.find returning
    async-iterable cursors that yield the given docs."""
    perm_docs = [{"key": k} for k in (persisted_perm_keys or [])]
    target_docs = [
        {"module": m, "entity": e, "event": ev}
        for (m, e, ev) in (persisted_targets or [])
    ]

    permissions = MagicMock()
    permissions.find = MagicMock(return_value=_make_async_cursor(perm_docs))

    policy_targets = MagicMock()
    policy_targets.find = MagicMock(return_value=_make_async_cursor(target_docs))
    policy_targets.update_one = AsyncMock()

    db = MagicMock()
    db.permissions = permissions
    db.policy_targets = policy_targets
    return db


def _make_app_doc(app_id=APP_ID, app_slug=APP_SLUG, name=APP_NAME, version=1):
    return {
        "app_id": app_id,
        "app_slug": app_slug,
        "name": name,
        "slug": app_slug.replace("_", "-"),
        "ingestion_version": version,
        "source_commit_sha": "abc123",
    }


def _make_catalogue(entity, fields, enums=None):
    return {
        "entity_name": entity,
        "fields": list(fields),
        "enums": enums or [],
    }


def _seed_field_registry(entries):
    """Drop in entries onto the in-memory field_registry dicts.

    entries: list of (module, entity, fields, events) tuples.
    Replaces any existing registration for the same (module, entity).
    """
    from services import field_registry as fr

    # Reset only the (module, entity) keys we care about so other
    # registrations (e.g. system modules from import-time decorators) stay.
    for module, entity, _, _ in entries:
        fr._registry.pop((module, entity), None)
        fr._event_registry.pop((module, entity), None)

    for module, entity, fields, events in entries:
        fr._registry[(module, entity)] = list(fields)
        fr._event_registry[(module, entity)] = list(events)


def _clear_field_registry_for(app_slug):
    from services import field_registry as fr

    for key in list(fr._registry.keys()):
        if key[0] == app_slug:
            fr._registry.pop(key, None)
    for key in list(fr._event_registry.keys()):
        if key[0] == app_slug:
            fr._event_registry.pop(key, None)


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


class TestReconcileService:

    def setup_method(self, _method):
        _clear_field_registry_for(APP_SLUG)

    def teardown_method(self, _method):
        _clear_field_registry_for(APP_SLUG)

    @pytest.mark.asyncio
    async def test_reconcile_app_not_found(self, tmp_path):
        from services.ingestion import reconcile, service

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=None)), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            with pytest.raises(ValueError, match=APP_ID):
                await reconcile.reconcile_app(APP_ID, performed_by="u1")

    @pytest.mark.asyncio
    async def test_reconcile_no_changes(self, tmp_path):
        """Runtime matches catalogue exactly → zero adds, zero flags."""
        from services.ingestion import reconcile, service

        fields = [
            {"name": "title", "type": "string"},
            {"name": "owner_id", "type": "string"},
            {"name": "status", "type": "string", "options": [
                {"value": "draft"}, {"value": "active"}
            ]},
        ]
        events = ["created", "updated", "deleted", "status_changed"]
        _seed_field_registry([(APP_SLUG, "project", fields, events)])

        catalogue = _make_catalogue(
            "project", fields,
            enums=[{"field_name": "status", "values": ["draft", "active"]}],
        )

        # Mapping on disk already has the matching status_enum so nothing drifts.
        import json
        (tmp_path / f"{APP_SLUG}.json").write_text(json.dumps({
            "app_slug": APP_SLUG,
            "entities": {
                "project": {
                    "collection": f"{APP_SLUG}_projects",
                    "id_field": "project_id",
                    "fields": {f["name"]: {"type": f["type"], "source_name": f["name"]} for f in fields},
                    "status_enum": ["draft", "active"],
                    "events": ["project_created", "project_updated", "project_deleted", "project_status_changed"],
                },
            },
        }))

        db = _make_db(
            persisted_perm_keys=[
                f"{APP_SLUG}.access",
                f"{APP_SLUG}.project.view",
                f"{APP_SLUG}.project.create",
                f"{APP_SLUG}.project.edit",
                f"{APP_SLUG}.project.delete",
                f"{APP_SLUG}.project.change_status",
            ],
            persisted_targets=[
                ("project", "project", "created"),
                ("project", "project", "updated"),
                ("project", "project", "deleted"),
                ("project", "project", "status_changed"),
            ],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()) as reg_perms, \
             patch.object(service, "_register_policy_targets", AsyncMock()) as reg_targets, \
             patch.object(service, "generate_mapping_file", AsyncMock()) as gen_map, \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        assert report["app_id"] == APP_ID
        assert report["summary"]["added"] == 0
        assert report["summary"]["flagged_for_review"] == 0
        assert report["changes"]["permissions_added"] == []
        assert report["changes"]["policy_targets_added"] == []
        assert report["changes"]["fields_added"] == []
        assert report["changes"]["fields_removed_flagged"] == []
        assert report["changes"]["enums_changed"] == []
        # Apply path didn't fire because nothing was missing.
        reg_perms.assert_not_called()
        reg_targets.assert_not_called()
        gen_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_detects_new_entity(self, tmp_path):
        """Runtime has an entity not in catalogue → adds CRUD perms + targets."""
        from services.ingestion import reconcile, service

        existing_fields = [{"name": "title", "type": "string"}]
        new_entity_fields = [
            {"name": "name", "type": "string"},
            {"name": "status", "type": "string"},
        ]
        events_new = ["created", "updated", "deleted", "status_changed"]
        _seed_field_registry([
            (APP_SLUG, "project", existing_fields, ["created", "updated", "deleted"]),
            (APP_SLUG, "task", new_entity_fields, events_new),
        ])

        catalogue = _make_catalogue("project", existing_fields)

        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[("project", "project", "created")],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()) as reg_perms, \
             patch.object(service, "_register_policy_targets", AsyncMock()) as reg_targets, \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        added_keys = {p["key"] for p in report["changes"]["permissions_added"]}
        assert f"{APP_SLUG}.task.view" in added_keys
        assert f"{APP_SLUG}.task.create" in added_keys
        assert f"{APP_SLUG}.task.edit" in added_keys
        assert f"{APP_SLUG}.task.delete" in added_keys
        # status field present → change_status permission included
        assert f"{APP_SLUG}.task.change_status" in added_keys

        target_pairs = {(t["module"], t["entity"], t["event"])
                        for t in report["changes"]["policy_targets_added"]}
        # New entity uses entity name as module (no catalogue exists yet),
        # matching the pipeline convention from service.py:472,510.
        assert ("task", "task", "created") in target_pairs
        assert ("task", "task", "status_changed") in target_pairs

        reg_perms.assert_called_once()
        reg_targets.assert_called()
        # source argument must be reconcile_auto
        call_kwargs = reg_perms.call_args.kwargs
        assert call_kwargs.get("source") == "reconcile_auto"

    @pytest.mark.asyncio
    async def test_reconcile_detects_new_field(self, tmp_path):
        """Runtime entity has a new field; mapping refresh fires; non-status fields don't add perms."""
        from services.ingestion import reconcile, service

        runtime_fields = [
            {"name": "title", "type": "string"},
            {"name": "owner_id", "type": "string"},
            {"name": "tags", "type": "list"},  # NEW
        ]
        _seed_field_registry([(APP_SLUG, "project", runtime_fields, ["created", "updated", "deleted"])])

        catalogue = _make_catalogue("project", [
            {"name": "title", "type": "string"},
            {"name": "owner_id", "type": "string"},
        ])
        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[
                ("project", "project", "created"),
                ("project", "project", "updated"),
                ("project", "project", "deleted"),
            ],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()), \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()) as gen_map, \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        added_fields = [f["field"] for f in report["changes"]["fields_added"]]
        assert "tags" in added_fields
        # Plain field add doesn't generate new permissions.
        assert report["changes"]["permissions_added"] == []
        assert report["changes"]["mapping_updated"] is True
        gen_map.assert_called()

    @pytest.mark.asyncio
    async def test_reconcile_flags_removed_field(self, tmp_path):
        """Catalogue has a field not in runtime → flagged, NOT deleted."""
        from services.ingestion import reconcile, service

        runtime_fields = [{"name": "title", "type": "string"}]
        _seed_field_registry([(APP_SLUG, "project", runtime_fields, ["created", "updated", "deleted"])])

        catalogue = _make_catalogue("project", [
            {"name": "title", "type": "string"},
            {"name": "legacy_ref", "type": "string"},  # removed from runtime
        ])
        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[
                ("project", "project", "created"),
                ("project", "project", "updated"),
                ("project", "project", "deleted"),
            ],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()) as reg_perms, \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        flagged_fields = [f["field"] for f in report["changes"]["fields_removed_flagged"]]
        assert "legacy_ref" in flagged_fields
        assert report["changes"]["permissions_added"] == []
        # No deactivation of permissions: register_permissions was never called.
        reg_perms.assert_not_called()
        # db.permissions.update_one is NOT called for the removed field.
        # (we exposed it as a MagicMock — just confirm reconcile didn't touch it)
        assert not hasattr(db.permissions, "update_one") or not db.permissions.update_one.called

    @pytest.mark.asyncio
    async def test_reconcile_detects_enum_change(self, tmp_path):
        """status_enum drift between mapping file and runtime field options → flagged."""
        from services.ingestion import reconcile, service

        runtime_fields = [
            {"name": "title", "type": "string"},
            {"name": "status", "type": "string", "options": [
                {"value": "draft"}, {"value": "active"}, {"value": "archived"}, {"value": "blocked"},
            ]},
        ]
        _seed_field_registry([(APP_SLUG, "project", runtime_fields,
                               ["created", "updated", "deleted", "status_changed"])])

        catalogue = _make_catalogue("project", runtime_fields,
                                    enums=[{"field_name": "status",
                                            "values": ["draft", "active", "archived", "blocked"]}])

        # Mapping file's status_enum is OUT OF DATE.
        import json
        (tmp_path / f"{APP_SLUG}.json").write_text(json.dumps({
            "app_slug": APP_SLUG,
            "entities": {
                "project": {
                    "collection": f"{APP_SLUG}_projects",
                    "id_field": "project_id",
                    "fields": {},
                    "status_enum": ["draft", "active"],  # OLD
                    "events": [],
                },
            },
        }))

        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[
                ("project", "project", "created"),
                ("project", "project", "updated"),
                ("project", "project", "deleted"),
                ("project", "project", "status_changed"),
            ],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()), \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        enum_changes = report["changes"]["enums_changed"]
        assert len(enum_changes) == 1
        change = enum_changes[0]
        assert change["entity"] == "project"
        assert change["field"] == "status"
        assert sorted(change["old"]) == ["active", "draft"]
        assert "blocked" in change["new"]

    @pytest.mark.asyncio
    async def test_reconcile_dry_run_does_not_apply(self, tmp_path):
        """dry_run=True → same report shape, but no DB writes."""
        from services.ingestion import reconcile, service

        new_entity_fields = [{"name": "name", "type": "string"}]
        _seed_field_registry([
            (APP_SLUG, "project", [{"name": "title", "type": "string"}], ["created", "updated", "deleted"]),
            (APP_SLUG, "widget", new_entity_fields, ["created", "updated", "deleted"]),
        ])

        catalogue = _make_catalogue("project", [{"name": "title", "type": "string"}])

        # Pre-seed a mapping file so _ensure_mapping doesn't trigger bootstrap.
        import json
        (tmp_path / f"{APP_SLUG}.json").write_text(json.dumps({
            "app_slug": APP_SLUG,
            "entities": {"project": {"status_enum": [], "fields": {}, "events": []}},
        }))

        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[("project", "project", "created")],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()) as write_log, \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()) as log_stage, \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()) as reg_perms, \
             patch.object(service, "_register_policy_targets", AsyncMock()) as reg_targets, \
             patch.object(service, "generate_mapping_file", AsyncMock()) as gen_map, \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1", dry_run=True)

        assert report["dry_run"] is True
        assert report["changes"]["permissions_added"], "expected to detect permissions"
        # Apply-path mocks must NOT be called.
        reg_perms.assert_not_called()
        reg_targets.assert_not_called()
        gen_map.assert_not_called()
        write_log.assert_not_called()
        log_stage.assert_not_called()
        # mapping_updated stays False on dry_run even though changes exist.
        assert report["changes"]["mapping_updated"] is False

    @pytest.mark.asyncio
    async def test_reconcile_idempotent(self, tmp_path):
        """Running twice produces the same result; second run reports zero adds."""
        from services.ingestion import reconcile, service

        runtime_fields = [
            {"name": "title", "type": "string"},
            {"name": "status", "type": "string", "options": [{"value": "draft"}, {"value": "active"}]},
        ]
        events = ["created", "updated", "deleted", "status_changed"]
        _seed_field_registry([(APP_SLUG, "project", runtime_fields, events)])

        catalogue = _make_catalogue("project", runtime_fields,
                                    enums=[{"field_name": "status", "values": ["draft", "active"]}])

        # State after the FIRST reconcile run — nothing more to add.
        import json
        (tmp_path / f"{APP_SLUG}.json").write_text(json.dumps({
            "app_slug": APP_SLUG,
            "entities": {
                "project": {
                    "collection": f"{APP_SLUG}_projects",
                    "id_field": "project_id",
                    "fields": {},
                    "status_enum": ["draft", "active"],
                    "events": [],
                },
            },
        }))

        db = _make_db(
            persisted_perm_keys=[
                f"{APP_SLUG}.access",
                f"{APP_SLUG}.project.view",
                f"{APP_SLUG}.project.create",
                f"{APP_SLUG}.project.edit",
                f"{APP_SLUG}.project.delete",
                f"{APP_SLUG}.project.change_status",
            ],
            persisted_targets=[("project", "project", ev) for ev in events],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()), \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            first = await reconcile.reconcile_app(APP_ID, performed_by="u1")
            # Mock cursors reset on every find() call — re-build the db so the second
            # run sees the same persisted state.
            db = _make_db(
                persisted_perm_keys=[
                    f"{APP_SLUG}.access",
                    f"{APP_SLUG}.project.view",
                    f"{APP_SLUG}.project.create",
                    f"{APP_SLUG}.project.edit",
                    f"{APP_SLUG}.project.delete",
                    f"{APP_SLUG}.project.change_status",
                ],
                persisted_targets=[("project", "project", ev) for ev in events],
            )
            with patch("services.ingestion.reconcile.get_db", return_value=db):
                second = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        for run in (first, second):
            assert run["summary"]["added"] == 0
            assert run["changes"]["permissions_added"] == []
            assert run["changes"]["policy_targets_added"] == []
            assert run["changes"]["fields_added"] == []

    @pytest.mark.asyncio
    async def test_reconcile_logs_audit_trail(self, tmp_path):
        """When applying, repo.write_log and repo.log_stage are both invoked."""
        from services.ingestion import reconcile, service

        new_fields = [{"name": "name", "type": "string"}]
        _seed_field_registry([
            (APP_SLUG, "project", [{"name": "title", "type": "string"}], ["created", "updated", "deleted"]),
            (APP_SLUG, "widget", new_fields, ["created", "updated", "deleted"]),
        ])

        catalogue = _make_catalogue("project", [{"name": "title", "type": "string"}])
        db = _make_db(
            persisted_perm_keys=[f"{APP_SLUG}.project.view"],
            persisted_targets=[("project", "project", "created")],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()) as write_log, \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()) as log_stage, \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()), \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            await reconcile.reconcile_app(APP_ID, performed_by="user_admin")

        write_log.assert_called_once()
        log_stage.assert_called_once()
        # performed_by propagates into the audit trail.
        log_stage_kwargs = log_stage.call_args.kwargs
        assert log_stage_kwargs.get("performed_by") == "user_admin"

    @pytest.mark.asyncio
    async def test_reconcile_status_field_on_existing_entity_proposes_change_status(self, tmp_path):
        """Adding a status field to an existing entity must propose its
        `.change_status` permission. Same for assignee fields and `.assign`.
        Without this, a dev who later adds a status field would have no
        change_status permission registered until full re-ingestion."""
        from services.ingestion import reconcile, service

        # Runtime: existing entity gained a `status` field.
        runtime_fields = [
            {"name": "title", "type": "string"},
            {"name": "status", "type": "string", "options": [
                {"value": "draft"}, {"value": "active"},
            ]},
        ]
        _seed_field_registry([(APP_SLUG, "project", runtime_fields,
                               ["created", "updated", "deleted", "status_changed"])])

        # Catalogue (from original ingestion) had no status field.
        catalogue = _make_catalogue("project", [{"name": "title", "type": "string"}])

        db = _make_db(
            persisted_perm_keys=[
                f"{APP_SLUG}.access",
                f"{APP_SLUG}.project.view",
                f"{APP_SLUG}.project.create",
                f"{APP_SLUG}.project.edit",
                f"{APP_SLUG}.project.delete",
                # change_status and assign were NOT generated at original
                # ingestion because the entity had no status/assignee field.
            ],
            persisted_targets=[("project", "project", ev) for ev in
                               ("created", "updated", "deleted", "status_changed")],
        )

        with patch("services.ingestion.reconcile.repo.get_app_registry",
                   AsyncMock(return_value=_make_app_doc())), \
             patch("services.ingestion.reconcile.repo.get_field_catalogues",
                   AsyncMock(return_value=[catalogue])), \
             patch("services.ingestion.reconcile.repo.write_log", AsyncMock()), \
             patch("services.ingestion.reconcile.repo.log_stage", AsyncMock()), \
             patch("services.ingestion.reconcile.get_db", return_value=db), \
             patch("services.ingestion.reconcile.permission_registry.register_permissions",
                   AsyncMock()), \
             patch.object(service, "_register_policy_targets", AsyncMock()), \
             patch.object(service, "generate_mapping_file", AsyncMock()), \
             patch.object(service, "MAPPINGS_DIR", tmp_path):
            report = await reconcile.reconcile_app(APP_ID, performed_by="u1")

        added_keys = {p["key"] for p in report["changes"]["permissions_added"]}
        # change_status MUST be proposed even though this is an existing entity.
        assert f"{APP_SLUG}.project.change_status" in added_keys, (
            f"Expected {APP_SLUG}.project.change_status to be proposed when a "
            f"status field is added to an existing entity. Got: {added_keys}"
        )
        # CRUD perms already exist — they must NOT be re-proposed.
        assert f"{APP_SLUG}.project.view" not in added_keys
        assert f"{APP_SLUG}.project.create" not in added_keys


class TestPermissionRegistryManualOverrideProtection:
    """register_permissions must skip keys whose persisted source is
    manual_override. Manual writes survive both ingestion and reconcile."""

    @pytest.mark.asyncio
    async def test_register_permissions_preserves_manual_override(self, monkeypatch):
        """Calling register_permissions with a key already stored as
        manual_override must NOT overwrite the source."""
        from services import permission_registry as pr

        # Track update_one calls and the find_one source returned.
        update_one_calls: list = []
        find_one_calls: list = []

        async def fake_find_one(query, projection=None):
            find_one_calls.append(query)
            # The "manual.override.key" is already stored as manual_override.
            if query.get("key") == "manual.override.key":
                return {"key": "manual.override.key", "source": pr.SOURCE_MANUAL_OVERRIDE}
            return None

        async def fake_update_one(query, update, upsert=False):
            update_one_calls.append((query, update, upsert))

        class FakePermissionsCollection:
            find_one = staticmethod(fake_find_one)
            update_one = staticmethod(fake_update_one)

        class FakeDB:
            permissions = FakePermissionsCollection()

        monkeypatch.setattr(pr, "get_db", lambda: FakeDB())

        registered = await pr.register_permissions(
            [
                {"key": "manual.override.key", "label": "Manually overridden"},
                {"key": "fresh.new.key", "label": "Fresh"},
            ],
            source=pr.SOURCE_RECONCILE_AUTO,
        )

        # Only the fresh key should have been written.
        assert registered == 1
        written_keys = [q.get("key") for q, _, _ in update_one_calls]
        assert "fresh.new.key" in written_keys
        assert "manual.override.key" not in written_keys, (
            "manual_override key must NOT be re-written during reconcile/ingestion"
        )


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def _make_user(user_id="user_admin_001"):
    u = SimpleNamespace()
    u.user_id = user_id
    u.email = "admin@example.com"
    return u


class TestReconcileEndpoints:

    @pytest.mark.asyncio
    async def test_reconcile_endpoint_requires_auth(self):
        """POST returns 403 when caller lacks admin.manage."""
        from routes.app_ingestion import reconcile_endpoint

        with patch("routes.app_ingestion.has_perm", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await reconcile_endpoint(app_id=APP_ID, current_user=_make_user())

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_reconcile_preview_endpoint_returns_dry_run_report(self):
        """GET preview wires through with dry_run=True."""
        from routes.app_ingestion import reconcile_preview_endpoint

        sample = {
            "app_id": APP_ID,
            "app_slug": APP_SLUG,
            "reconciled_at": "2026-04-29T00:00:00+00:00",
            "dry_run": True,
            "changes": {
                "permissions_added": [],
                "policy_targets_added": [],
                "fields_added": [],
                "fields_removed_flagged": [],
                "enums_changed": [],
                "mapping_updated": False,
            },
            "summary": {"added": 0, "flagged_for_review": 0, "no_change": 1},
        }

        with patch("routes.app_ingestion.has_perm", return_value=True), \
             patch("services.ingestion.reconcile.reconcile_app",
                   AsyncMock(return_value=sample)) as mock_recon:
            result = await reconcile_preview_endpoint(
                app_id=APP_ID, current_user=_make_user()
            )

        assert result == sample
        # confirm dry_run=True passed through
        assert mock_recon.call_args.kwargs.get("dry_run") is True

    @pytest.mark.asyncio
    async def test_reconcile_endpoint_app_not_found(self):
        """ValueError from service → 404."""
        from routes.app_ingestion import reconcile_endpoint

        with patch("routes.app_ingestion.has_perm", return_value=True), \
             patch("services.ingestion.reconcile.reconcile_app",
                   AsyncMock(side_effect=ValueError(f"App {APP_ID} not found"))):
            with pytest.raises(HTTPException) as exc_info:
                await reconcile_endpoint(app_id=APP_ID, current_user=_make_user())

        assert exc_info.value.status_code == 404
        assert APP_ID in exc_info.value.detail
