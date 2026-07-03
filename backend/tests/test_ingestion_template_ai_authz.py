"""
Ingestion template AI authz coverage test.

Code generation during app ingestion is performed by Claude reading the canonical
templates in `docs/ingestion/01e-adaptation-tooling.md` and the `ingest-app` skill,
not by a programmatic generator. These templates therefore *are* the spec.

This test pins the contract: every route example in those templates must gate AI
callers via `Depends(require_ai_permission(...))` on the route decorator and import
the dep from `middleware.ai_authz`. If a future edit drops the dep from a route, this
test fails — preventing regressions where ingested apps emit routes that bypass the
AI RBAC gate.

Scope: FUTURE ingestions only. Already-ingested route files under `backend/routes/`
are not touched by this test.
"""
import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical template files that future ingestions read from.
TEMPLATE_FILES = [
    REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md",
    REPO_ROOT / ".claude" / "skills" / "ingest-app" / "SKILL.md",
    REPO_ROOT / "docs" / "ingestion" / "01c-pipeline-stages.md",
]


def _read(path: Path) -> str:
    assert path.exists(), f"Template file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_templates_import_require_ai_permission():
    """Every ingestion template must import the AI authz dep."""
    for path in TEMPLATE_FILES:
        text = _read(path)
        assert "from middleware.ai_authz import require_ai_permission" in text, (
            f"{path} is missing 'from middleware.ai_authz import require_ai_permission'. "
            "Future ingestions must import this dep so generated routes can gate AI callers."
        )


@pytest.mark.parametrize(
    "method,perm_action",
    [
        ("get", "view"),
        ("post", "create"),
        ("put", "edit"),
        ("delete", "delete"),
    ],
)
def test_canonical_template_routes_include_ai_dep(method, perm_action):
    """
    The canonical Template 1 in 01e-adaptation-tooling.md must show every CRUD
    handler with `dependencies=[Depends(require_ai_permission(...))]` on the
    decorator. We grep for the dep within a window after each `@router.<method>`.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")

    pattern = re.compile(
        rf"@router\.{method}\(\s*[^)]*?dependencies=\[Depends\(require_ai_permission\(",
        re.DOTALL,
    )
    matches = pattern.findall(text)
    assert matches, (
        f"No @router.{method}(...) with dependencies=[Depends(require_ai_permission(...))] "
        f"found in 01e-adaptation-tooling.md. Future ingestions need this on every {method.upper()} "
        f"route to gate AI callers (action: {perm_action})."
    )


def test_skill_documents_operation_to_perm_mapping():
    """The ingest-app skill must document the operation -> perm key mapping."""
    text = _read(REPO_ROOT / ".claude" / "skills" / "ingest-app" / "SKILL.md")
    # Spot-check a few of the action keywords appear together with the dep usage.
    for action in ("create", "edit", "delete", "view"):
        assert action in text, f"Skill template missing action '{action}' in operation->perm mapping"
    assert "require_ai_permission" in text, "Skill template missing require_ai_permission usage"


def test_canonical_template_keeps_human_has_perm_check():
    """
    Defense-in-depth: the AI dep is *additive*. The human `has_perm(...)` check in
    the handler body must remain — that's the human gate. If a future edit replaces
    has_perm with the AI dep alone, this test fails.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")
    # The canonical Template 1 should still call has_perm in handler bodies.
    assert text.count('has_perm(current_user, "{app_slug}.{entity_plural}.') >= 4, (
        "Template 1 lost human-side has_perm checks. The AI dep is additive — "
        "human callers still go through has_perm in the handler body."
    )


# ---------------------------------------------------------------------------
# crud_envelope coverage — Wave 2 routes through `services.ingestion.route_helpers`
# to emit policy → event → audit in a single block. The canonical template must
# show every state-changing handler wrapping its DB op in `async with crud_envelope`
# and import the helper. Status-change detection must appear in the update handler.
# ---------------------------------------------------------------------------


def test_templates_import_crud_envelope():
    """Every ingestion template must import the crud_envelope helper."""
    for path in TEMPLATE_FILES:
        text = _read(path)
        assert "from services.ingestion.route_helpers import crud_envelope" in text, (
            f"{path} is missing 'from services.ingestion.route_helpers import "
            "crud_envelope'. Future ingestions must import the envelope so generated "
            "state-changing routes emit policy → event → audit through one coordinator."
        )


@pytest.mark.parametrize(
    "method,action",
    [
        ("post", "created"),
        ("put", "updated"),
        ("delete", "deleted"),
    ],
)
def test_canonical_template_state_changing_routes_use_envelope(method, action):
    """
    Every state-changing handler in Template 1 must wrap its DB op in
    `async with crud_envelope(...)` with the matching action. We grep for the
    `@router.<method>` decorator and verify a `crud_envelope` block opens
    within the handler body that follows.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")

    # Find the @router.<method>(...) decorator + the body that follows it,
    # stopping at the next decorator or end-of-fence so we don't span handlers.
    pattern = re.compile(
        rf"@router\.{method}\([^)]*?\).*?(?=@router\.|```)",
        re.DOTALL,
    )
    handler_blocks = pattern.findall(text)
    assert handler_blocks, (
        f"No @router.{method}(...) handler found in 01e-adaptation-tooling.md "
        f"to check for crud_envelope wrapping."
    )

    # At least one handler block for this method must show the envelope opening
    # with the matching action. Templates may show multiple handlers per method
    # (e.g. two GETs); for state changes there is one canonical handler each.
    matched = [
        block for block in handler_blocks
        if "async with crud_envelope(" in block and f'action="{action}"' in block
    ]
    assert matched, (
        f"@router.{method}(...) handler in Template 1 does not wrap its DB op "
        f"in `async with crud_envelope(... action=\"{action}\" ...)`. "
        f"Future ingestions must route state changes through the envelope so "
        f"policy + event + audit fire in one coordinated block."
    )


def test_canonical_template_get_handlers_skip_envelope():
    """
    Reads must NOT use crud_envelope — they're not state changes. Verify the
    GET handlers in Template 1 do not contain `crud_envelope(`.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")
    pattern = re.compile(
        r"@router\.get\([^)]*?\).*?(?=@router\.|```)",
        re.DOTALL,
    )
    get_blocks = pattern.findall(text)
    assert get_blocks, "No @router.get(...) handlers found in Template 1."
    for block in get_blocks:
        assert "crud_envelope(" not in block, (
            "A @router.get(...) handler in Template 1 wraps its body in "
            "crud_envelope. Reads are not state changes — the envelope is for "
            "create/update/delete only."
        )


def test_canonical_template_update_has_status_change_detection():
    """
    The update handler in Template 1 must show the status-change detection
    pattern: after the primary `updated` envelope closes, compare
    before.status vs updated.status and open a second `crud_envelope` with
    action="status_changed" when they differ.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")
    # Grab the PUT handler block.
    pattern = re.compile(
        r"@router\.put\([^)]*?\).*?(?=@router\.|```)",
        re.DOTALL,
    )
    put_blocks = pattern.findall(text)
    assert put_blocks, "No @router.put(...) handler found in Template 1."

    found = False
    for block in put_blocks:
        has_status_compare = (
            'before.get("status") != updated.get("status")' in block
            or "before.get('status') != updated.get('status')" in block
        )
        has_status_envelope = 'action="status_changed"' in block
        if has_status_compare and has_status_envelope:
            found = True
            break

    assert found, (
        "Template 1's update handler is missing status-change detection. "
        "When the entity has a status field and it changes during update, "
        "a second crud_envelope with action=\"status_changed\" must fire "
        "after the primary `updated` envelope so workflow rules can react."
    )


def test_canonical_template_state_changing_handlers_take_request():
    """
    State-changing handlers must accept `request: Request` so the envelope can
    correlate multiple events (e.g. updated + status_changed) under one
    request_id. If the request param is missing, the envelope falls back to a
    generated id and correlation is lost.
    """
    text = _read(REPO_ROOT / "docs" / "ingestion" / "01e-adaptation-tooling.md")
    for method in ("post", "put", "delete"):
        pattern = re.compile(
            rf"@router\.{method}\([^)]*?\).*?(?=@router\.|```)",
            re.DOTALL,
        )
        blocks = pattern.findall(text)
        assert blocks, f"No @router.{method}(...) handler found in Template 1."
        # At least one handler for this method must declare `request: Request`.
        with_request = [b for b in blocks if "request: Request" in b]
        assert with_request, (
            f"@router.{method}(...) handler in Template 1 does not take "
            f"`request: Request`. The envelope needs the Request object to "
            f"share request_id across multiple envelopes in one HTTP call."
        )
