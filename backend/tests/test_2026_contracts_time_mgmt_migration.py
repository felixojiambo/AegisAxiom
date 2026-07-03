"""Wave 0 (contracts-time-mgmt) — migration / index foundation locks.

Verifies the Wave 0 migration script:

  1. Creates every new collection enumerated in NEW_COLLECTIONS.
  2. Creates every index in the documented plan, with the documented
     options (unique / sparse flags preserved).
  3. Is idempotent — running twice does not raise and does not create
     duplicate index entries on the recording fake.
  4. Touches only schema. Existing contract and time_entry documents
     are not read, mutated, moved, or removed.

The tests run against an in-process fake Mongo recorder so the suite
does not require a live database. No new test framework is added; the
fake is a small inline class in the same style as the existing
test_ai_tool_enforcement.fake_db fixture.

The migration file's name (``2026_contracts_time_mgmt_schema.py``)
starts with a digit, which is not a valid Python identifier, so the
file is loaded via importlib.util.spec_from_file_location instead of
``from migrations.2026... import``. That matches how digit-prefixed
migration files are typically loaded across the Python ecosystem.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "2026_contracts_time_mgmt_schema.py"
)


def _load_migration_module():
    """Load the digit-prefixed migration file as a module object."""
    spec = importlib.util.spec_from_file_location(
        "mig_2026_contracts_time_mgmt", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Minimal in-process Mongo recorder. Records every collection creation
# and every create_index call so the tests can assert on them without a
# live database. Mirrors the surface the migration touches:
#   db.list_collection_names()
#   db.create_collection(name)
#   db[name].create_index(keys, **options)
# ---------------------------------------------------------------------------


class _RecordingCollection:
    def __init__(self, name: str, owner: "_RecordingDB"):
        self._name = name
        self._owner = owner

    async def create_index(self, keys, **options):
        self._owner.index_calls.append((self._name, keys, dict(options)))
        # Track by name so duplicate-name idempotency can be verified.
        name = options.get("name") or repr(keys)
        self._owner.indexes_by_name.setdefault(self._name, {})[name] = (
            keys,
            dict(options),
        )
        # Pretend a real driver would have returned the index name.
        return name


class _RecordingDB:
    """A fake Motor-shaped db. Records what the migration did and
    accepts repeat calls without raising — matching the idempotency
    behavior the real Motor driver gives us for already-existing
    collections/indexes with identical specs."""

    def __init__(self, preexisting_collections=None):
        self.collections = set(preexisting_collections or [])
        self.created_collections: list = []
        self.index_calls: list = []
        # collection_name -> {index_name -> (keys, options)}
        self.indexes_by_name: dict = {}
        # Sentinel rows the migration must NOT mutate. Read-only contracts
        # collection used to confirm zero document mutations.
        self.read_log: list = []

    async def list_collection_names(self):
        return sorted(self.collections)

    async def create_collection(self, name: str):
        if name in self.collections:
            # Mimic NamespaceExists; migration swallows and verifies.
            raise RuntimeError(f"collection already exists: {name}")
        self.collections.add(name)
        self.created_collections.append(name)

    def __getitem__(self, name: str) -> _RecordingCollection:
        # First access registers the collection as accessible (matches
        # Motor's lazy behavior). It does NOT create the collection
        # in Mongo's sense; that requires create_collection or an
        # insert. Important for confirming the migration doesn't touch
        # business documents.
        return _RecordingCollection(name, self)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_creates_every_new_collection():
    module = _load_migration_module()
    db = _RecordingDB()
    await module.apply_contracts_time_mgmt_schema(db)
    for name in module.NEW_COLLECTIONS:
        assert name in db.collections, f"migration did not create {name}"
    # And the public helper agrees with the constant.
    assert tuple(module._safe_collection_list()) == module.NEW_COLLECTIONS


@pytest.mark.asyncio
async def test_migration_indexes_every_planned_entry():
    module = _load_migration_module()
    db = _RecordingDB()
    result = await module.apply_contracts_time_mgmt_schema(db)
    plan = module._safe_index_plan()
    assert result["indexes_ensured"] == len(plan)
    # Every (collection, keys, options) in the plan must appear once
    # in the recorder's create_index call log.
    for collection, keys, options in plan:
        match = (collection, keys, options)
        assert match in db.index_calls, (
            f"missing create_index call for {collection} with keys={keys} "
            f"options={options}"
        )


@pytest.mark.asyncio
async def test_migration_indexes_contracts_and_time_entries():
    """Spot-check the index plan for the two pre-existing collections.
    These collections are not in NEW_COLLECTIONS — they already exist
    in production — but the migration adds secondary indexes to them."""
    module = _load_migration_module()
    db = _RecordingDB()
    await module.apply_contracts_time_mgmt_schema(db)
    contract_indexes = db.indexes_by_name.get("contracts", {})
    assert "account_id_1_status_1" in contract_indexes
    assert "deal_ids_1" in contract_indexes
    assert "project_ids_1" in contract_indexes
    assert "approval_status_1_status_1" in contract_indexes
    assert "tenant_id_1_created_at_-1" in contract_indexes
    time_indexes = db.indexes_by_name.get("time_entries", {})
    assert "contract_id_1_status_1_week_start_1" in time_indexes
    assert "pm_task_id_1" in time_indexes


@pytest.mark.asyncio
async def test_unique_indexes_carry_unique_flag():
    """contract_time_applications.entry_id, invoice_batches and
    zoho_* identity indexes must be created with unique=True."""
    module = _load_migration_module()
    db = _RecordingDB()
    await module.apply_contracts_time_mgmt_schema(db)
    unique_indexes = {
        ("contract_time_applications", "entry_id_1"),
        ("zoho_contracts", "zoho_id_1"),
        ("zoho_projects", "zoho_id_1"),
    }
    for collection, idx_name in unique_indexes:
        keys, options = db.indexes_by_name[collection][idx_name]
        assert options.get("unique") is True, (
            f"{collection}.{idx_name} should be unique"
        )


@pytest.mark.asyncio
async def test_external_contract_id_is_sparse_unique():
    """external_contract_id must be sparse so legacy contract rows
    without that field do not violate the uniqueness constraint."""
    module = _load_migration_module()
    db = _RecordingDB()
    await module.apply_contracts_time_mgmt_schema(db)
    keys, options = db.indexes_by_name["contracts"]["external_contract_id_1"]
    assert options.get("unique") is True
    assert options.get("sparse") is True


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_second_run():
    """Running the migration twice must not raise and must not produce
    extra distinct index entries (same name → same spec)."""
    module = _load_migration_module()
    db = _RecordingDB()
    await module.apply_contracts_time_mgmt_schema(db)
    first_collections = set(db.collections)
    first_index_count = sum(len(v) for v in db.indexes_by_name.values())
    # Second run — should not raise, should not add new collections.
    await module.apply_contracts_time_mgmt_schema(db)
    assert db.collections == first_collections, (
        "second run added unexpected collections"
    )
    second_index_count = sum(len(v) for v in db.indexes_by_name.values())
    assert second_index_count == first_index_count, (
        "second run produced a different number of distinct indexes"
    )


@pytest.mark.asyncio
async def test_migration_does_not_mutate_existing_business_documents():
    """The migration must not call find/insert/update/delete on any
    collection. Recorder does not implement those methods — if the
    migration tries, AttributeError surfaces immediately."""
    module = _load_migration_module()
    db = _RecordingDB(preexisting_collections=["contracts", "time_entries"])

    # The recorder's collection proxy only exposes create_index. Any
    # attempt to call find/insert/update/delete would raise here.
    await module.apply_contracts_time_mgmt_schema(db)

    # contracts and time_entries existed before — the migration must
    # not have re-created them.
    assert "contracts" not in db.created_collections
    assert "time_entries" not in db.created_collections


@pytest.mark.asyncio
async def test_index_plan_covers_every_required_pair():
    """Explicit lock against the Wave 0 spec — every (collection,
    leading-key) pair the spec calls out must appear in the plan. If a
    later refactor renames an entry, this test fails fast."""
    module = _load_migration_module()
    plan_pairs = set()
    for collection, keys, _opts in module._safe_index_plan():
        leading = keys if isinstance(keys, str) else keys[0][0]
        plan_pairs.add((collection, leading))
    required = {
        ("contract_approvals", "contract_id"),
        ("contract_adjustments", "contract_id"),
        ("contract_billing_triggers", "contract_id"),
        ("contract_time_applications", "contract_id"),
        ("contract_time_applications", "entry_id"),
        ("invoice_batches", "contract_id"),
        ("invoice_batches", "status"),
        ("zoho_contracts", "zoho_id"),
        ("zoho_projects", "zoho_id"),
        ("contracts", "account_id"),
        ("contracts", "deal_ids"),
        ("contracts", "project_ids"),
        ("contracts", "approval_status"),
        ("contracts", "external_contract_id"),
        ("contracts", "tenant_id"),
        ("time_entries", "contract_id"),
        ("time_entries", "pm_task_id"),
    }
    missing = required - plan_pairs
    assert not missing, f"index plan missing required pairs: {missing}"


def test_module_exposes_callable_entry_point():
    """apply_contracts_time_mgmt_schema must be importable and async,
    so callers (cold-start hook, tests, CLI) have a single entry."""
    module = _load_migration_module()
    fn = getattr(module, "apply_contracts_time_mgmt_schema", None)
    assert fn is not None
    assert asyncio.iscoroutinefunction(fn)
