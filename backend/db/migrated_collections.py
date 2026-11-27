"""The registry of Mongo collections migrated to PostgreSQL.

The data lives in ``migrated_collections.json``, next to this module. A data
file, not Python, because ``scripts/verify.ps1`` and ``scripts/verify.sh``
read the same file to build the zero-grep patterns. One list drives both the
static gate (grep) and the runtime gate (``services/workflow_executor``),
so the two cannot drift apart.

Adding a collection is the phase's step 4 (``/phase-cutover``). Entries are
never removed: a migrated collection stays migrated.

The file is validated at import. A malformed registry raises here, which
fails boot loudly instead of silently weakening the guard.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional

REGISTRY_PATH = Path(__file__).with_name("migrated_collections.json")

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PHASE = re.compile(r"^[0-9]+[a-z]?$")


def _load(path: Path = REGISTRY_PATH) -> Mapping[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("collections")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path.name}: 'collections' must be a non-empty list")
    out: dict[str, str] = {}
    for i, entry in enumerate(entries):
        name = entry.get("name") if isinstance(entry, dict) else None
        phase = entry.get("phase") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"{path.name}: entry {i} has an invalid name {name!r}")
        if not isinstance(phase, str) or not _PHASE.match(phase):
            raise ValueError(f"{path.name}: entry {i} ({name}) has an invalid phase {phase!r}")
        if name in out:
            raise ValueError(f"{path.name}: duplicate collection {name!r}")
        out[name] = phase
    return MappingProxyType(out)


#: collection name -> the phase that migrated it, e.g. {"roles": "2b"}.
MIGRATED_COLLECTIONS: Mapping[str, str] = _load()


def migrated_in(collection: str) -> Optional[str]:
    """The phase that migrated ``collection``, or None if it is still on Mongo."""
    return MIGRATED_COLLECTIONS.get(collection)
