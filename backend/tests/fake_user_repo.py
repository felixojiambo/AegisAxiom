"""In-memory stand-in for `repositories.users.UserRepository`.

Phase 2a completion: `users` moved to Postgres, so the AI mention-resolver tools
no longer read `db["users"]` — they open a short-lived session via
`conversational_tools._pg_users()`. These tools are unit-tested with fake
collections and no database, so this provides the same shape without one.

The real methods are exercised against real Postgres in
`tests/test_platform_core_projects_users_pg.py`; this fake exists only so the
tool-logic tests (ambiguity handling, project scoping, short-circuiting) stay
fast and DB-free. Its matching semantics deliberately mirror the SQL:

  * case-insensitive, prefix or contains, on `name` OR `email`
  * `%` and `_` are LITERAL — the real query escapes them, because the search
    term arrives from a promptable LLM
  * results ordered by name, then limited
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

_MODULE = "services.ai.agents.tools.conversational_tools"


class FakeUserRepo:
    def __init__(self, docs):
        self._docs = [dict(d) for d in (docs or [])]

    @staticmethod
    def _card(doc):
        return {
            "user_id": doc.get("user_id"),
            "name": doc.get("name"),
            "email": doc.get("email"),
        }

    async def search_user_cards(self, term, *, prefix_only=True, user_ids=None, limit=10):
        if user_ids is not None:
            if not user_ids:
                return []
            allowed = set(user_ids)
        else:
            allowed = None

        needle = (term or "").lower()
        out = []
        for d in self._docs:
            if allowed is not None and d.get("user_id") not in allowed:
                continue
            fields = [str(d.get("name") or "").lower(), str(d.get("email") or "").lower()]
            hit = any(f.startswith(needle) for f in fields) if prefix_only \
                else any(needle in f for f in fields)
            if hit:
                out.append(self._card(d))
        out.sort(key=lambda c: c["name"] or "")
        return out[:limit]

    async def list_user_cards_by_ids(self, user_ids, *, limit=20):
        ids = set(user_ids or [])
        if not ids:
            return []
        out = [self._card(d) for d in self._docs if d.get("user_id") in ids]
        out.sort(key=lambda c: c["name"] or "")
        return out[:limit]


def patched_pg_users(docs):
    """`with patched_pg_users([...]):` — `_pg_users()` yields a FakeUserRepo."""

    @asynccontextmanager
    async def _fake():
        yield FakeUserRepo(docs)

    return patch(f"{_MODULE}._pg_users", _fake)


_current_user_map: list = []


def set_current_user_map(docs):
    """Publish the user docs `patched_load_user_map()` should serve.

    Accepts a list of docs or a {user_id: doc} mapping — the AP-draft tests use
    both shapes.
    """
    global _current_user_map
    if isinstance(docs, dict):
        docs = list(docs.values())
    _current_user_map = [dict(d) for d in (docs or [])]


def patched_load_user_map():
    """Patch `services.finance_draft_autogen._load_user_map`.

    Phase 2a completion: the AP-draft builder prefetches its users from Postgres
    in one query instead of issuing `db["users"].find_one(...)` per time entry.
    These tests are Mongo-shaped unit tests with no database, so the prefetch is
    served from the same doc set they already declare. Values are attribute-
    accessed (`.name`, `.contractor_business_id`) because the real prefetch
    returns `User` models, not dicts.
    """

    async def _fake(user_ids):
        wanted = {u for u in (user_ids or []) if u}
        return {
            d["user_id"]: SimpleNamespace(**d)
            for d in _current_user_map
            if d.get("user_id") in wanted
        }

    return patch("services.finance_draft_autogen._load_user_map", _fake)


_current_docs: list = []


def set_current_user_docs(docs):
    """Publish the user docs `patched_pg_users_live()` should serve."""
    global _current_docs
    _current_docs = [dict(d) for d in (docs or [])]


def patched_pg_users_live():
    """Like `patched_pg_users`, but resolves the docs at call time.

    For test files whose fake DB is constructed inside the test body, after any
    fixture has run. Install once per module with an autouse fixture and have
    the fake-DB constructor call `set_current_user_docs(...)`; the patch then
    serves whichever user set the test just built.
    """

    @asynccontextmanager
    async def _fake():
        yield FakeUserRepo(_current_docs)

    return patch(f"{_MODULE}._pg_users", _fake)
