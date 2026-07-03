"""Wave 5 hardening — invoice batch + finance draft index migration.

Locks the two sparse-unique indexes the Wave 5 hardening migration
adds:

  invoice_batches.batch_key       sparse unique
  finance_drafts.source_batch_id  sparse unique

Both are sparse so legacy rows that pre-date the migration (no
batch_key field, no source_batch_id field) do not violate the
constraint. Both indexes survive a second run without raising.

The migration filename starts with a digit so it must be loaded via
importlib.util.spec_from_file_location — same pattern as the Wave 0
migration test.
"""

from __future__ import annotations

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
    / "2026_invoice_batches_wave5_indexes.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "mig_2026_invoice_batches_wave5", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingCollection:
    def __init__(self, name, owner):
        self._name = name
        self._owner = owner

    async def create_index(self, keys, **options):
        self._owner.index_calls.append((self._name, keys, dict(options)))
        name = options.get("name") or repr(keys)
        self._owner.indexes_by_name.setdefault(self._name, {})[name] = (keys, dict(options))
        return name


class _RecordingDB:
    def __init__(self):
        self.index_calls = []
        self.indexes_by_name = {}

    def __getitem__(self, name):
        return _RecordingCollection(name, self)


@pytest.mark.asyncio
async def test_migration_creates_both_sparse_unique_indexes():
    module = _load_migration()
    db = _RecordingDB()
    result = await module.apply_invoice_batches_wave5_indexes(db)
    assert result["indexes_ensured"] == len(module._safe_index_plan())

    # invoice_batches.batch_key
    ib = db.indexes_by_name["invoice_batches"]["batch_key_1"]
    keys, options = ib
    assert keys == "batch_key"
    assert options.get("unique") is True
    assert options.get("sparse") is True

    # finance_drafts.source_batch_id
    fd = db.indexes_by_name["finance_drafts"]["source_batch_id_1"]
    keys, options = fd
    assert keys == "source_batch_id"
    assert options.get("unique") is True
    assert options.get("sparse") is True


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_second_run():
    module = _load_migration()
    db = _RecordingDB()
    await module.apply_invoice_batches_wave5_indexes(db)
    first_count = sum(len(v) for v in db.indexes_by_name.values())
    await module.apply_invoice_batches_wave5_indexes(db)
    second_count = sum(len(v) for v in db.indexes_by_name.values())
    # Same indexes; map keyed by name; second call must not add new entries.
    assert first_count == second_count
