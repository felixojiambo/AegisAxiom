"""Per-contract approver overrides — pure helper module.

These helpers wrap the ``contract_approver_overrides`` Mongo
collection so the route layer never embeds the query shape. Keeping
the shape behind a function lets the index choice change without
hunting through routes/contracts.py.

Persistence shape (one document = one user × one contract grant):

    {
      "override_id":      "ovr_<12-hex>",
      "tenant_id":        "tenant_…" | None,
      "contract_id":      "contract_…",
      "user_id":          "user_…",
      "state":            "active" | "revoked",
      "note":             str | None,
      "granted_by":       "user_…",
      "granted_at":       datetime (utc),
      "revoked_by":       "user_…" | None,
      "revoked_at":       datetime (utc) | None,
      "revoke_note":      str | None,
      "created_at":       datetime (utc),
      "updated_at":       datetime (utc),
    }

Indexes are declared in
``backend/migrations/2026_contracts_per_contract_overrides_indexes.py``.

Important semantics:

* A user can have at most one ``active`` row per (tenant_id,
  contract_id, user_id). The grant path enforces this in code
  (re-activate the existing row instead of inserting a duplicate)
  because some Mongo deploys don't reliably honor partial unique
  indexes; the index plan still includes the partial unique for
  defense in depth.
* Cross-tenant lookup fails closed — every public helper takes a
  ``tenant_id`` and refuses to match a row whose ``tenant_id``
  differs. ``None`` matches ``None`` (single-tenant deploys).
* The helpers never touch contract documents. The route layer
  remains responsible for contract-level tenancy + RBAC.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def gen_override_id() -> str:
    return f"ovr_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_match_clause(tenant_id: Optional[str]) -> Dict[str, Any]:
    """Compose a tenant-matching clause. ``None`` matches documents
    whose ``tenant_id`` is missing or null — that's the
    single-tenant case the rest of Contracts handles symmetrically."""
    if tenant_id is None or tenant_id == "":
        return {"$or": [
            {"tenant_id": None},
            {"tenant_id": {"$exists": False}},
        ]}
    return {"tenant_id": tenant_id}


def _collection(db):
    """Return the contract_approver_overrides collection handle, or
    ``None`` when the database does not expose that attribute.

    Test harnesses occasionally use stripped-down fake DBs that only
    model the collections their suite needs. Falling back to ``None``
    when the collection is missing keeps the per-contract override
    helpers safe to call from any route — the read paths simply
    return their no-match equivalent (False / empty list) instead
    of raising ``AttributeError``. Production deploys always have
    the collection because the schema migration creates it.
    """
    return getattr(db, "contract_approver_overrides", None)


async def has_active_override(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    user_id: str,
) -> bool:
    """True when the user holds an active per-contract approver
    override for this contract in this tenant. Pure read. Cross-
    tenant rows never match. Returns False when the
    ``contract_approver_overrides`` collection isn't present on the
    db handle (defensive — see ``_collection`` above)."""
    if not contract_id or not user_id:
        return False
    coll = _collection(db)
    if coll is None:
        return False
    query = {
        "$and": [
            _tenant_match_clause(tenant_id),
            {"contract_id": contract_id},
            {"user_id": user_id},
            {"state": "active"},
        ]
    }
    doc = await coll.find_one(query, {"_id": 1})
    return doc is not None


async def list_overridden_contract_ids(
    db,
    *,
    tenant_id: Optional[str],
    user_id: str,
) -> List[str]:
    """Return the contract_ids for which the user holds an active
    per-contract override in this tenant. Used to scope the
    approval queue for override-only users. Returns ``[]`` when the
    collection is absent (defensive — see ``_collection`` above)."""
    if not user_id:
        return []
    coll = _collection(db)
    if coll is None:
        return []
    query = {
        "$and": [
            _tenant_match_clause(tenant_id),
            {"user_id": user_id},
            {"state": "active"},
        ]
    }
    cursor = coll.find(query, {"_id": 0, "contract_id": 1})
    rows = await cursor.to_list(500)
    seen: set = set()
    out: List[str] = []
    for row in rows:
        cid = row.get("contract_id")
        if isinstance(cid, str) and cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


async def list_overrides_for_contract(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    include_revoked: bool = True,
) -> List[Dict[str, Any]]:
    """List override rows attached to a contract, newest first.
    Used by the per-contract management UI to display history."""
    if not contract_id:
        return []
    coll = _collection(db)
    if coll is None:
        return []
    base_clauses: List[Dict[str, Any]] = [
        _tenant_match_clause(tenant_id),
        {"contract_id": contract_id},
    ]
    if not include_revoked:
        base_clauses.append({"state": "active"})
    cursor = coll.find({"$and": base_clauses}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(500)


async def find_active_override(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the full active override row (or None). The grant path
    uses this to re-activate an existing row instead of inserting a
    duplicate."""
    if not contract_id or not user_id:
        return None
    coll = _collection(db)
    if coll is None:
        return None
    query = {
        "$and": [
            _tenant_match_clause(tenant_id),
            {"contract_id": contract_id},
            {"user_id": user_id},
            {"state": "active"},
        ]
    }
    return await coll.find_one(query, {"_id": 0})


async def find_any_override_row(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Return any row for this (tenant, contract, user) tuple
    regardless of state. The grant path uses this to detect a
    previously revoked row that should be re-activated rather
    than re-inserted (avoids unique-index conflicts on partial
    unique indexes and keeps the audit chain on one row)."""
    if not contract_id or not user_id:
        return None
    coll = _collection(db)
    if coll is None:
        return None
    query = {
        "$and": [
            _tenant_match_clause(tenant_id),
            {"contract_id": contract_id},
            {"user_id": user_id},
        ]
    }
    return await coll.find_one(query, {"_id": 0})


async def grant_override(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    user_id: str,
    granted_by: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Grant or reactivate a per-contract override. Idempotent: a
    repeat grant for the same (tenant, contract, user) tuple
    updates ``note`` + ``granted_by`` + ``granted_at`` on the
    existing row rather than inserting a duplicate.

    Returns the resulting override document.
    """
    now = _now()
    note_value = (note or "").strip() or None
    coll = _collection(db)
    if coll is None:
        raise RuntimeError(
            "contract_approver_overrides collection is not available "
            "on this database handle — run migration "
            "2026_contracts_per_contract_overrides_indexes.py."
        )
    existing = await find_any_override_row(
        db, tenant_id=tenant_id, contract_id=contract_id, user_id=user_id,
    )
    if existing is not None:
        update_set: Dict[str, Any] = {
            "state": "active",
            "note": note_value,
            "granted_by": granted_by,
            "granted_at": now,
            "revoked_by": None,
            "revoked_at": None,
            "revoke_note": None,
            "updated_at": now,
        }
        await coll.update_one(
            {"override_id": existing["override_id"]},
            {"$set": update_set},
        )
        return {**existing, **update_set}

    doc: Dict[str, Any] = {
        "override_id": gen_override_id(),
        "tenant_id": tenant_id,
        "contract_id": contract_id,
        "user_id": user_id,
        "state": "active",
        "note": note_value,
        "granted_by": granted_by,
        "granted_at": now,
        "revoked_by": None,
        "revoked_at": None,
        "revoke_note": None,
        "created_at": now,
        "updated_at": now,
    }
    await coll.insert_one(dict(doc))
    return doc


async def revoke_override(
    db,
    *,
    tenant_id: Optional[str],
    contract_id: str,
    user_id: str,
    revoked_by: str,
    revoke_note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Soft-revoke the active override for (tenant, contract, user).
    Returns the post-update row, or None when no active row existed.

    Soft-revoke (state -> "revoked") instead of delete so the audit
    chain stays attached to a single row across grant / revoke
    cycles.
    """
    coll = _collection(db)
    if coll is None:
        return None
    active = await find_active_override(
        db, tenant_id=tenant_id, contract_id=contract_id, user_id=user_id,
    )
    if active is None:
        return None
    now = _now()
    note_value = (revoke_note or "").strip() or None
    update_set: Dict[str, Any] = {
        "state": "revoked",
        "revoked_by": revoked_by,
        "revoked_at": now,
        "revoke_note": note_value,
        "updated_at": now,
    }
    await coll.update_one(
        {"override_id": active["override_id"]},
        {"$set": update_set},
    )
    return {**active, **update_set}


__all__ = [
    "gen_override_id",
    "has_active_override",
    "list_overridden_contract_ids",
    "list_overrides_for_contract",
    "find_active_override",
    "find_any_override_row",
    "grant_override",
    "revoke_override",
]
