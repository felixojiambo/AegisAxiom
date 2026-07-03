"""Pure-helper tests for services/contract_approval_access.

This is the algorithm that drives the admin Contract Approval
Access page. Pins:

  - approval_source resolution from role permissions + per-user
    extra_permissions (role / override / role_and_override / none)
  - search across name / email / role / user_id
  - approval_filter buckets (all / can_approve / role / override
    / none) and their mutual exclusivity
  - page + page_size clamping, page bounds, total counting
  - deterministic sort (can_approve first, then name, then email)

No DB, no FastAPI, no Mongo client — pure inputs/outputs only.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

from services.contract_approval_access import (  # noqa: E402
    ALLOWED_PAGE_SIZES,
    CONTRACT_APPROVE_KEY,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    VALID_APPROVAL_FILTERS,
    coerce_approval_filter,
    coerce_page,
    coerce_page_size,
    filter_and_paginate,
    resolve_approval_access,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _user(user_id, name, email, role, extras=None):
    return {
        "user_id": user_id,
        "name": name,
        "email": email,
        "role": role,
        "extra_permissions": list(extras or []),
    }


def _role(name, perms):
    return {"name": name, "permissions": list(perms)}


@pytest.fixture
def roles():
    return [
        _role("admin", ["*"]),
        _role("director", ["contracts.approve", "users.view"]),
        _role("finance", ["contracts.approve"]),
        _role("sales_rep", ["opportunities.view", "deals.view"]),
        _role("contractor", []),
    ]


@pytest.fixture
def users():
    return [
        _user("u-admin-1", "Alice Admin", "alice@x.com", "admin"),
        _user("u-director-1", "Dan Director", "dan@x.com", "director"),
        _user("u-finance-1", "Fern Finance", "fern@x.com", "finance"),
        # Sales rep with direct override -> override only.
        _user("u-sales-1", "Sam Sales", "sam@x.com", "sales_rep",
              extras=["contracts.approve", "deals.export"]),
        # Sales rep without override -> none.
        _user("u-sales-2", "Sara Sales", "sara@x.com", "sales_rep"),
        # Finance with extra override on top of role -> role_and_override.
        _user("u-finance-2", "Frank Finance", "frank@x.com", "finance",
              extras=["contracts.approve", "audit.view"]),
        # Contractor with no perms -> none.
        _user("u-contractor-1", "Carl Contractor", "carl@x.com", "contractor"),
    ]


# ---------------------------------------------------------------------------
# resolve_approval_access
# ---------------------------------------------------------------------------


def test_resolve_handles_role_only(roles):
    rows = resolve_approval_access(
        [_user("u1", "D", "d@x.com", "director")], roles,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["approval_source"] == "role"
    assert row["has_role_permission"] is True
    assert row["has_override_permission"] is False
    assert row["can_approve"] is True


def test_resolve_handles_override_only(roles):
    rows = resolve_approval_access(
        [_user("u1", "S", "s@x.com", "sales_rep", extras=["contracts.approve"])],
        roles,
    )
    assert rows[0]["approval_source"] == "override"
    assert rows[0]["has_role_permission"] is False
    assert rows[0]["has_override_permission"] is True
    assert rows[0]["can_approve"] is True


def test_resolve_handles_role_and_override(roles):
    rows = resolve_approval_access(
        [_user("u1", "F", "f@x.com", "finance", extras=["contracts.approve"])],
        roles,
    )
    assert rows[0]["approval_source"] == "role_and_override"
    assert rows[0]["has_role_permission"] is True
    assert rows[0]["has_override_permission"] is True
    assert rows[0]["can_approve"] is True


def test_resolve_handles_none(roles):
    rows = resolve_approval_access(
        [_user("u1", "S", "s@x.com", "sales_rep")], roles,
    )
    assert rows[0]["approval_source"] == "none"
    assert rows[0]["has_role_permission"] is False
    assert rows[0]["has_override_permission"] is False
    assert rows[0]["can_approve"] is False


def test_admin_wildcard_role_counts_as_role_grant(roles):
    rows = resolve_approval_access(
        [_user("u1", "A", "a@x.com", "admin")], roles,
    )
    assert rows[0]["has_role_permission"] is True
    assert rows[0]["approval_source"] == "role"


def test_resolve_skips_malformed_users(roles):
    """Defensive: bad rows must not crash the page — drop them."""
    rows = resolve_approval_access(
        [
            None,
            "not-a-dict",
            {"user_id": "", "name": "no id"},
            {"name": "no id field"},
            {"user_id": 42, "name": "non-string id"},
            _user("u1", "A", "a@x.com", "admin"),
        ],
        roles,
    )
    assert len(rows) == 1
    assert rows[0]["user_id"] == "u1"


def test_resolve_tolerates_unknown_role(roles):
    """A user whose role isn't in the roles collection is treated as
    having no role permissions — they just need an override."""
    rows = resolve_approval_access(
        [
            _user("u1", "X", "x@x.com", "unknown_role"),
            _user("u2", "Y", "y@x.com", "unknown_role",
                  extras=["contracts.approve"]),
        ],
        roles,
    )
    assert rows[0]["approval_source"] == "none"
    assert rows[1]["approval_source"] == "override"


def test_resolve_tolerates_no_roles_at_all(users):
    rows = resolve_approval_access(users, [])
    # No role grants. Anyone with the override -> override; nobody
    # else can approve.
    by_id = {r["user_id"]: r for r in rows}
    assert by_id["u-director-1"]["approval_source"] == "none"
    assert by_id["u-finance-2"]["approval_source"] == "override"
    assert by_id["u-sales-1"]["approval_source"] == "override"


# ---------------------------------------------------------------------------
# coerce_* helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("all", "all"),
    ("ALL", "all"),
    ("  can_approve  ", "can_approve"),
    ("role", "role"),
    ("override", "override"),
    ("none", "none"),
    ("invalid", "all"),
    ("", "all"),
    (None, "all"),
    (42, "all"),
])
def test_coerce_approval_filter(value, expected):
    assert coerce_approval_filter(value) == expected


def test_valid_approval_filters_set_is_locked():
    assert VALID_APPROVAL_FILTERS == ("all", "can_approve", "role", "override", "none")


@pytest.mark.parametrize("raw,expected", [
    (1, 1),
    (5, 5),
    ("3", 3),
    (None, 1),
    ("abc", 1),
    (0, 1),
    (-3, 1),
])
def test_coerce_page(raw, expected):
    assert coerce_page(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (25, 25),
    (50, 50),
    (100, 100),
    ("50", 50),
    # Anything not in ALLOWED_PAGE_SIZES collapses to DEFAULT so the
    # response shape stays deterministic and the UI's <select> stays
    # the canonical source of allowed values.
    (33, DEFAULT_PAGE_SIZE),
    (200, DEFAULT_PAGE_SIZE),
    (0, DEFAULT_PAGE_SIZE),
    (-5, DEFAULT_PAGE_SIZE),
    (None, DEFAULT_PAGE_SIZE),
    ("not-a-number", DEFAULT_PAGE_SIZE),
])
def test_coerce_page_size(raw, expected):
    assert coerce_page_size(raw) == expected


def test_default_page_size_constants_stable():
    """Pin the defaults so the UI and the API don't drift."""
    assert DEFAULT_PAGE_SIZE == 50
    assert MAX_PAGE_SIZE == 100
    assert set(ALLOWED_PAGE_SIZES) == {25, 50, 100}


# ---------------------------------------------------------------------------
# filter_and_paginate — happy paths
# ---------------------------------------------------------------------------


def test_filter_pagination_envelope_shape(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows)
    assert set(result) == {"items", "page", "page_size", "total"}
    assert isinstance(result["items"], list)
    assert isinstance(result["total"], int)
    assert result["total"] == len(users)
    assert result["page"] == 1
    assert result["page_size"] == DEFAULT_PAGE_SIZE


def test_filter_all_returns_every_user(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="all")
    assert result["total"] == len(users)


def test_filter_can_approve_returns_everyone_who_can_approve(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="can_approve")
    # admin, director, finance, sales(override), finance(role+override) — 5
    expected_ids = {
        "u-admin-1", "u-director-1", "u-finance-1", "u-sales-1", "u-finance-2",
    }
    assert {r["user_id"] for r in result["items"]} == expected_ids
    assert result["total"] == len(expected_ids)


def test_filter_role_excludes_role_and_override(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="role")
    # Pure role bucket: admin, director, finance (no override).
    assert {r["user_id"] for r in result["items"]} == {
        "u-admin-1", "u-director-1", "u-finance-1",
    }
    # Finance + extra override does NOT match the pure "role"
    # bucket — that's the whole point of the four-way split.
    assert "u-finance-2" not in {r["user_id"] for r in result["items"]}


def test_filter_override_excludes_role_and_override(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="override")
    # Pure override bucket: sales rep with override only.
    assert {r["user_id"] for r in result["items"]} == {"u-sales-1"}
    assert "u-finance-2" not in {r["user_id"] for r in result["items"]}


def test_filter_none_returns_users_with_no_access(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="none")
    assert {r["user_id"] for r in result["items"]} == {
        "u-sales-2", "u-contractor-1",
    }


def test_filter_buckets_are_mutually_exclusive_and_exhaustive(users, roles):
    """Sum of role + override + role_and_override + none must equal
    the total user count, with no overlap."""
    rows = resolve_approval_access(users, roles)
    by_source = {"role": set(), "override": set(),
                 "role_and_override": set(), "none": set()}
    for r in rows:
        by_source[r["approval_source"]].add(r["user_id"])
    # No user is in two buckets.
    all_pairs = list(by_source.values())
    for i, a in enumerate(all_pairs):
        for b in all_pairs[i + 1:]:
            assert not (a & b)
    # Union covers every user.
    union = set().union(*all_pairs)
    assert union == {u["user_id"] for u in users}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_matches_name(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, search="dan")
    assert {r["user_id"] for r in result["items"]} == {"u-director-1"}


def test_search_matches_email(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, search="frank@")
    assert {r["user_id"] for r in result["items"]} == {"u-finance-2"}


def test_search_matches_role(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, search="sales_rep")
    assert {r["user_id"] for r in result["items"]} == {"u-sales-1", "u-sales-2"}


def test_search_is_case_insensitive(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, search="ALICE")
    assert {r["user_id"] for r in result["items"]} == {"u-admin-1"}


def test_search_blank_matches_everyone(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, search="   ")
    assert result["total"] == len(users)


def test_search_and_filter_compose(users, roles):
    """Search filters first, then approval_filter narrows further."""
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(
        rows, search="sales", approval_filter="can_approve",
    )
    assert {r["user_id"] for r in result["items"]} == {"u-sales-1"}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_returns_first_page_at_size_2(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, page=1, page_size=25)
    assert result["page"] == 1
    assert result["page_size"] == 25
    assert len(result["items"]) <= 25


def test_pagination_returns_subsequent_pages():
    # 60 users, 25 per page -> page 1 = 25, page 2 = 25, page 3 = 10.
    bulk_users = [
        _user(f"u-{i:03d}", f"User {i:03d}", f"u{i:03d}@x.com", "sales_rep")
        for i in range(60)
    ]
    bulk_rows = resolve_approval_access(bulk_users, [])
    page1 = filter_and_paginate(bulk_rows, page=1, page_size=25)
    page2 = filter_and_paginate(bulk_rows, page=2, page_size=25)
    page3 = filter_and_paginate(bulk_rows, page=3, page_size=25)
    assert page1["total"] == page2["total"] == page3["total"] == 60
    assert len(page1["items"]) == 25
    assert len(page2["items"]) == 25
    assert len(page3["items"]) == 10
    # And pages don't overlap.
    ids = (
        {r["user_id"] for r in page1["items"]}
        | {r["user_id"] for r in page2["items"]}
        | {r["user_id"] for r in page3["items"]}
    )
    assert len(ids) == 60


def test_pagination_out_of_bounds_returns_empty_items(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, page=99, page_size=25)
    assert result["items"] == []
    assert result["total"] == len(users)


def test_pagination_coerces_invalid_page_size(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, page=1, page_size="not-a-number")
    assert result["page_size"] == DEFAULT_PAGE_SIZE


def test_pagination_clamps_negative_page(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, page=-2, page_size=25)
    assert result["page"] == 1


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_sort_can_approve_first(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, page_size=100)
    # The first N items have can_approve=True; once a False shows up,
    # no True follows.
    saw_false = False
    for r in result["items"]:
        if not r["can_approve"]:
            saw_false = True
        else:
            assert not saw_false, (
                "can_approve=True user appeared after a can_approve=False "
                "user — sort guarantee broken."
            )


def test_sort_within_bucket_alphabetical_by_name(users, roles):
    rows = resolve_approval_access(users, roles)
    result = filter_and_paginate(rows, approval_filter="can_approve", page_size=100)
    names = [r["name"].lower() for r in result["items"]]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Constants the route relies on
# ---------------------------------------------------------------------------


def test_contract_approve_key_is_dotted():
    """The wire format for the permission key must remain `contracts.approve`
    — auth_helper.has_perm and the user extra_permissions array both depend
    on this exact string."""
    assert CONTRACT_APPROVE_KEY == "contracts.approve"
