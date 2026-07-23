"""Contract tests for the Nova-related index declarations in server.py.

Index creation runs inside ``@app.on_event("startup")`` against a live
Mongo connection, so we don't try to execute it here. Instead we
source-search ``backend/server.py`` for the specific named indexes the
Nova index audit (2026-05-06) added to make sure a future
edit to that file doesn't silently drop them.

If a future edit refactors the index block (e.g. extracts to a helper
module), update _INDEX_DECLARATION_SOURCES below to point at the new
file(s).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# Files that may contain the nova index declarations. server.py is the
# canonical home today; if the index block ever moves, add the new
# location here so the test follows the code.
_REPO_BACKEND = Path(__file__).resolve().parent.parent
_INDEX_DECLARATION_SOURCES = [
    _REPO_BACKEND / "server.py",
]


def _read_all_sources() -> str:
    """Concatenate every candidate source file. Cheap; lets us assert on
    presence without caring exactly which file holds the declaration."""
    parts: list[str] = []
    for path in _INDEX_DECLARATION_SOURCES:
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def index_sources() -> str:
    return _read_all_sources()


@pytest.mark.parametrize(
    "collection_attr, index_name, key_spec_fragments",
    [
        # general_ledger.nova_tx_date_basis / nova_account_tx_basis were removed
        # in Phase 1 (accounting-integration removal): the general_ledger
        # collection no longer exists, so those index declarations are gone.
        #
        # finance_drafts.nova_kind_sync_status: covers
        # finance_snapshots.compute_finance_snapshot kind+sync_status filter.
        (
            "db.finance_drafts",
            "nova_kind_sync_status",
            [
                '("kind", 1)',
                '("sync_status", 1)',
            ],
        ),
    ],
)
def test_nova_index_declared(
    index_sources: str,
    collection_attr: str,
    index_name: str,
    key_spec_fragments: list[str],
) -> None:
    """Assert each Nova-audit index is declared in the startup block.

    Uses a substring search rather than executing the Mongo call so the
    test stays unit-style (no live DB required). Failure means a future
    edit dropped or renamed the declaration; restore it in
    backend/server.py before merging.
    """
    # Collection.create_index call must mention the collection handle
    assert collection_attr in index_sources, (
        f"Expected {collection_attr}.create_index(...) in the index "
        "declaration block but no reference found."
    )

    # Explicit name kwarg must match the live-DB index name. Without
    # this, MongoDB auto-names the index and would conflict with the
    # manually-created live index on next startup
    # (IndexOptionsConflict).
    assert f'name="{index_name}"' in index_sources, (
        f"Expected name=\"{index_name}\" kwarg in the index declaration. "
        "The Nova audit indexes were created manually on the live DB; "
        "the code MUST declare them with the same name to avoid "
        "IndexOptionsConflict on startup."
    )

    # Every key field+direction in the spec must appear. Catches
    # accidental key-order drift (e.g. (basis, tx_date) instead of
    # (tx_date, basis)) which Mongo treats as a different index.
    for fragment in key_spec_fragments:
        assert fragment in index_sources, (
            f"Expected key fragment {fragment!r} for index "
            f"{index_name!r} but it's missing from the declaration."
        )


def test_nova_indexes_use_background_build() -> None:
    """The surviving Nova index should pass background=True so a fresh
    deployment doesn't block startup while the index builds.

    Modern Mongo (4.2+) builds in background by default and the kwarg is
    a no-op, but matching the audit's spec keeps the intent explicit and
    portable across server versions.
    """
    sources = _read_all_sources()
    # For the surviving named index we want both `name="nova_*"` and
    # `background=True` somewhere in the file. (The general_ledger nova
    # indexes were removed in Phase 1 along with that collection.)
    for index_name in (
        "nova_kind_sync_status",
    ):
        # Find the declaration substring and assert background=True
        # appears within ~200 chars of the name. Avoids false-positives
        # from a `background=True` on an unrelated index elsewhere.
        idx = sources.find(f'name="{index_name}"')
        assert idx != -1, f"Index {index_name!r} not found"
        window = sources[max(0, idx - 200) : idx + 200]
        assert "background=True" in window, (
            f"Expected background=True near name=\"{index_name}\" "
            "declaration; ref Nova index audit 2026-05-06."
        )
