"""Regression tests for the Create Project wizard payload.

User-reported bug: completing the Create Project wizard with a
template selected fails at submit with:

    422 Unprocessable Entity
    detail: [{"loc": ["body", "project_type"], "msg": "Field required"}]

even though the wizard's Methodology step already collected the
methodology. Audit traced the failure to the template-path body
assembly in CreateProjectWizard.js — it was sending every required
field EXCEPT `project_type` to POST /api/projects/projects/from-template.
The blank-path body (POST /api/projects/projects) already included
project_type; only the from-template path was missing it.

This file locks the fix at two layers:

  1. Pydantic level — confirm `ProjectFromTemplateCreate` continues
     to require `project_type` so a future model edit can't quietly
     make this field optional and re-introduce the silent submit-
     failure mode.
  2. Source level on the wizard — confirm the template-path body
     now includes `project_type: methodology` (not just the blank
     path) so the JSON shipped to /from-template matches the model.

yarn / node_modules are unavailable in the build sandbox; the
React component cannot run through Jest here. The contract is
verified via source inspection — same pattern as the prior wiring
tests on this branch.
"""

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Backend-side: lock the Pydantic contract that the wizard now matches.
# ---------------------------------------------------------------------------


def test_project_from_template_create_still_requires_project_type():
    """The Pydantic model that backs /projects/from-template must
    keep `project_type` as a required field. If a future commit
    makes it Optional, the wizard's omission would silently start
    succeeding again and downstream code paths that branch on
    project_type (waterfall/agile/waves_threads) would fail later
    with less actionable errors."""
    from pydantic import ValidationError
    from routes.projects_project import ProjectFromTemplateCreate

    with pytest.raises(ValidationError) as ei:
        ProjectFromTemplateCreate(
            name="Test project",
            service_line="professional_services",
            template_id="tpl_test_1",
            # project_type intentionally omitted to assert it is required
        )
    # The error path must include "project_type" so a caller can see
    # which field is missing.
    errors = ei.value.errors()
    assert any(
        "project_type" in (err.get("loc") or ())
        and err.get("type") == "missing"
        for err in errors
    ), (
        "ProjectFromTemplateCreate must require project_type. If a "
        "future refactor wants to default it from the template, that "
        "decision belongs on the model AND the wizard contract must "
        "be updated in the same commit."
    )


def test_project_from_template_create_accepts_canonical_wizard_payload():
    """Positive lock: the exact payload shape the wizard now sends
    (after the fix) must instantiate ProjectFromTemplateCreate
    without error. This is the bytes-on-the-wire contract — if a
    future field rename on the model breaks parity with the wizard,
    this test surfaces it before the user does."""
    from routes.projects_project import ProjectFromTemplateCreate

    payload = ProjectFromTemplateCreate(
        name="Acme Q4 Migration",
        project_type="waterfall",
        service_line="professional_services",
        pm_id="user_test_1",
        template_id="tpl_test_1",
        start_date="2026-06-01",
        target_end_date="2026-09-30",
    )
    assert payload.project_type == "waterfall"
    assert payload.template_id == "tpl_test_1"


def test_project_create_already_requires_project_type():
    """Sanity lock on the blank-path model too. The wizard's blank
    path already sent project_type before this fix; this test
    guarantees the contract stays stable so the blank flow doesn't
    silently break in the opposite direction."""
    from pydantic import ValidationError
    from routes.projects_project import ProjectCreate

    with pytest.raises(ValidationError) as ei:
        ProjectCreate(
            name="Test",
            service_line="professional_services",
        )
    errors = ei.value.errors()
    assert any(
        "project_type" in (err.get("loc") or ())
        and err.get("type") == "missing"
        for err in errors
    )


# ---------------------------------------------------------------------------
# Frontend-side: source-level lock that the wizard's template path
# now sends project_type.
# ---------------------------------------------------------------------------


def _wizard_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "projects"
        / "CreateProjectWizard.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_wizard_template_path_payload_includes_project_type():
    """The bug was specifically the template-path body in submit().
    The blank-path body already had `project_type: methodology`;
    the from-template path did not. After the fix, the from-template
    POST body must also include `project_type: methodology` (not a
    hardcoded literal, not a different field name)."""
    src = _wizard_src()

    # Locate the from-template POST so we can isolate its body.
    needle = "api.post(`${API_URL}/api/projects/projects/from-template`"
    post_idx = src.find(needle)
    assert post_idx != -1, (
        "Wizard must POST to /api/projects/projects/from-template "
        "on the template path."
    )

    # Walk backwards to the enclosing `body = {` literal.
    body_open = src.rfind("body = {", 0, post_idx)
    assert body_open != -1, (
        "Could not locate the template-path body literal preceding "
        "the from-template POST."
    )

    template_body_block = src[body_open:post_idx]

    # The body must reference project_type and it must be set from
    # the methodology state, not a hardcoded literal like 'waterfall'.
    assert "project_type: methodology" in template_body_block, (
        "Template-path body must include `project_type: methodology` "
        "so the from-template route's required field is satisfied. "
        "The blank path already does this; the template path was the "
        "regression."
    )
    # Defensive lock: do not hardcode a methodology value here.
    for hardcoded in (
        "project_type: 'waterfall'",
        "project_type: \"waterfall\"",
        "project_type: 'agile'",
        "project_type: \"agile\"",
        "project_type: 'waves_threads'",
        "project_type: \"waves_threads\"",
    ):
        assert hardcoded not in template_body_block, (
            f"Template-path body must not hardcode {hardcoded!r}; "
            "the methodology state drives the value."
        )


def test_wizard_blank_path_still_sends_project_type():
    """Negative regression — the fix must not accidentally remove or
    rename `project_type` on the blank path."""
    src = _wizard_src()

    needle = "api.post(`${API_URL}/api/projects/projects`"
    post_idx = src.find(needle)
    assert post_idx != -1, (
        "Wizard must POST to /api/projects/projects on the blank path."
    )

    body_open = src.rfind("body = {", 0, post_idx)
    assert body_open != -1
    blank_body_block = src[body_open:post_idx]

    assert "project_type: methodology" in blank_body_block, (
        "Blank-path body must keep `project_type: methodology` — "
        "removing it would re-introduce the same 422 on the blank "
        "flow."
    )


def test_wizard_does_not_use_aliased_project_type_field_name():
    """Defensive lock against future drift: if a refactor introduces
    a camelCase alias like `projectType:` anywhere in the wizard
    submit body, the backend (which only accepts snake_case
    project_type) would 422 silently. Pin the snake_case name."""
    src = _wizard_src()
    assert "projectType:" not in src, (
        "Wizard body fields must use the canonical snake_case "
        "`project_type:` — `projectType:` is the wrong shape for the "
        "backend Pydantic model."
    )
