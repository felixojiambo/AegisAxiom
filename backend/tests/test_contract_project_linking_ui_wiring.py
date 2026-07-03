"""Source-level wiring contract for the Contract Projects UI panel.

Backend project-linking routes (covered by
backend/tests/test_contract_project_linking.py and
backend/tests/test_contract_ps_project_autocreate.py):

    POST   /api/contracts/{id}/projects
           body: { project_ids: [str, ...] }
           returns the updated contract
    DELETE /api/contracts/{id}/projects/{project_id}
           idempotent — returns 200 with the unchanged contract
           when the id was never linked

Perm gates (mirror routes/contracts.py):
    link / unlink : contracts.edit | contracts.edit.all
                  | contracts.edit.own

Optional candidate-name / search routes (used only when the caller
ALSO holds projects.project.view):
    GET /api/projects/{id}       — resolve a linked id to a doc
    GET /api/projects?q=…&limit= — search candidates by name

This file locks the new ContractProjectsPanel so a future refactor
cannot silently drift the page-to-route bindings or invent fields,
candidate routes, or project-management actions outside the panel's
narrow link/unlink scope.

yarn / node_modules are unavailable in the build sandbox, so the
React panel cannot run through Jest here. The contract is verified
via source inspection — same pattern as the prior Contracts UI
wiring tests on this branch (billing triggers, adjustments,
approval queue, approval panel, invoice batches).
"""

from pathlib import Path

import pytest


def _panel_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "contracts"
        / "sections"
        / "ContractProjectsPanel.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _modal_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "contracts"
        / "modals"
        / "ContractDetailsModal.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_panel_exports_default_component():
    src = _panel_src()
    assert "export default function ContractProjectsPanel(" in src, (
        "ContractProjectsPanel.js must export a default function "
        "component named ContractProjectsPanel."
    )


def test_panel_link_and_unlink_routes_match_backend_exactly():
    """Lock both contract routes. A typo silently 404s — the backend
    has no fallback for either."""
    src = _panel_src()

    # POST attach — full path template + body field name.
    assert (
        "api.post(" in src
        and "/api/contracts/${contractId}/projects`" in src
    ), (
        "Link must use api.post against "
        "/api/contracts/{id}/projects."
    )
    assert "{ project_ids: idsToSubmit }" in src, (
        "Link request body must use exactly the canonical "
        "`project_ids` field — backend rejects any other name."
    )

    # DELETE detach — full path template with both interpolations.
    assert (
        "api.delete(" in src
        and "/api/contracts/${contractId}/projects/${pid}`"
        in src
    ), (
        "Unlink must use api.delete against "
        "/api/contracts/{id}/projects/{project_id}."
    )


def test_panel_uses_canonical_link_unlink_permission_keys():
    """The contracts route gates link / unlink on the union:
        contracts.edit | contracts.edit.all | contracts.edit.own
    Wildcard '*' admin grant is honored separately. The UI must
    check the same union to avoid silent 403s after a click."""
    src = _panel_src()
    for needle in (
        "hasPermission('contracts.edit')",
        "hasPermission('contracts.edit.all')",
        "hasPermission('contracts.edit.own')",
    ):
        assert (needle in src) or (needle.replace("'", '"') in src), (
            f"canEdit gate must check {needle!r}."
        )
    assert "hasPermission('*')" in src or 'hasPermission("*")' in src, (
        "canEdit must also admit hasPermission('*') for admin-tier "
        "users."
    )


def test_panel_falls_back_to_manual_id_without_project_view_perm():
    """The candidate-search and per-id resolve routes require
    projects.project.view. Without it the panel must NOT invent a
    fake picker — it must fall back to a plain project_id input
    labeled clearly so the user can paste an id and link."""
    src = _panel_src()
    # canSeeProjects gates the picker AND the per-id resolve calls.
    assert "canSeeProjects" in src, (
        "Panel must compute a canSeeProjects flag from "
        "hasPermission('projects.project.view')."
    )
    assert "hasPermission('projects.project.view')" in src, (
        "canSeeProjects must check the exact backend permission key."
    )
    # The manual id input must exist as the fallback path.
    assert "contract-projects-manual-id" in src, (
        "Panel must expose a manual project_id input "
        "(data-testid=contract-projects-manual-id) as the fallback "
        "when projects.project.view is missing."
    )
    # Picker mode default must depend on canSeeProjects.
    assert "useState(canSeeProjects)" in src, (
        "Picker-mode default must initialize from canSeeProjects so "
        "the search UI is hidden when the caller lacks the view perm."
    )


def test_panel_search_and_resolve_use_canonical_project_routes():
    """When projects.project.view is granted, the panel resolves
    candidate project names via the canonical search + get routes.

    The path is intentionally doubled — the projects_project router
    is mounted with `prefix="/projects"` in server.py (line 261) and
    its own route literals also start with `/projects` (see
    backend/routes/projects_project.py:362, 407). The full external
    URLs are therefore /api/projects/projects and
    /api/projects/projects/{id}, not /api/projects/{id}. A previous
    revision of this panel hit the singular path and 404'd silently;
    this lock prevents that regression.
    """
    src = _panel_src()
    assert "/api/projects/projects/${id}" in src, (
        "Per-id resolve must hit /api/projects/projects/{id} — the "
        "router prefix /projects + the route literal /projects/{id} "
        "compose to the doubled path. A bare /api/projects/{id} "
        "would 404."
    )
    assert "/api/projects/projects?q=${encodeURIComponent(q)}&limit=10" in src, (
        "Candidate search must hit /api/projects/projects?q=…&limit= "
        "for the same prefix+literal reason."
    )
    # Defensive negative lock — bare /api/projects/{id} or
    # /api/projects?q=… anywhere in the panel would be the same bug
    # we just fixed. The contract-side link/unlink routes legitimately
    # use /api/contracts/{id}/projects (different router, no prefix
    # double), so those are NOT covered by this lock.
    assert "/api/projects/${id}" not in src, (
        "Panel must not call /api/projects/{id} — that bare path "
        "404s because the projects router is mounted at "
        "/api/projects with /projects route literals."
    )
    assert "/api/projects?q=${encodeURIComponent" not in src, (
        "Panel must not call /api/projects?q=… — same reason."
    )


def test_panel_reads_linked_ids_from_contract_project_ids_field():
    """The contract carries linked project ids as `project_ids` (a
    list of strings). The panel must read from this canonical field
    only — no invented `linked_projects` / `project_links` array."""
    src = _panel_src()
    assert "contract?.project_ids" in src, (
        "Panel must read linked ids from contract.project_ids."
    )
    # Negative locks — no invented field names.
    for invented in (
        "contract.linked_projects",
        "contract.project_links",
        "contract.linked_project_ids",
        "contract.attached_projects",
    ):
        assert invented not in src, (
            f"Panel must not invent a contract field {invented!r} — "
            "backend stores linked ids on contract.project_ids only."
        )


def test_panel_has_loading_empty_and_error_states():
    src = _panel_src()
    for testid in (
        "contract-projects-loading",
        "contract-projects-empty",
        "contract-projects-error",
    ):
        assert testid in src, (
            f"Panel must render the {testid!r} state."
        )


def test_panel_refreshes_contract_after_link_and_unlink():
    """Both routes return the full refreshed contract. The panel
    must propagate that to the parent modal so other panels (basic
    info, financials, etc.) reflect the new project_ids list. The
    parent contract reference also drives the linked-list rendering
    in this panel."""
    src = _panel_src()
    # Each mutator path passes the refreshed contract upward.
    assert src.count("onContractUpdated?.(updated);") >= 2, (
        "Both link and unlink success paths must call "
        "onContractUpdated?.(updated) so the contract refreshes in "
        "place across the modal."
    )


def test_panel_does_not_invent_project_management_actions():
    """This slice only links / unlinks existing projects. The panel
    must NOT pretend to create / edit / delete / archive projects —
    those live in the Project Management module and are out of
    scope for the Contracts UI."""
    src = _panel_src()
    # The only allowed verbs on /api/projects from this panel are
    # GET (resolve + search). No POST/PATCH/DELETE on /projects.
    # Negative locks on patterns that would betray invented
    # project-management actions.
    forbidden_patterns = (
        # Inventing project-create from the contract panel.
        "api.post(`${API}/api/projects`",
        "api.post(`${API}/api/projects/${",
        # Inventing project-edit from the contract panel.
        "api.patch(`${API}/api/projects",
        # Inventing project-delete from the contract panel
        # (DELETE /api/projects/{id} would be project deletion;
        # the contract-side detach is /contracts/{cid}/projects/{pid}).
        "api.delete(`${API}/api/projects/${",
    )
    for pattern in forbidden_patterns:
        assert pattern not in src, (
            f"Panel must not call {pattern!r} — this is the contracts "
            "project-linking slice, not project management."
        )


def test_panel_unlink_is_per_row_not_bulk():
    """The backend exposes a per-project DELETE — there is no bulk
    detach. The UI must mirror that so a user understands the
    granularity of the action."""
    src = _panel_src()
    # The per-row unlink testid is parameterized by project id.
    assert "contract-projects-unlink-${pid}" in src, (
        "Unlink button must carry a per-id testid "
        "(contract-projects-unlink-{pid}) so the action is clearly "
        "per-row, not bulk."
    )
    # Negative lock — no `project_ids` array in the DELETE call.
    # The DELETE URL must reference a SINGLE ${pid}, never a list.
    assert "project_ids: pids" not in src, (
        "Panel must not attempt a bulk-detach body shape — the "
        "backend has no bulk DELETE route."
    )


def test_panel_link_body_is_an_array_field():
    """Backend POST body shape is exactly `{ project_ids: [...] }`.
    The panel may submit one or many ids in a single request, but
    they must go through the `project_ids` array field — never a
    scalar `project_id` value, which the route would 400 on."""
    src = _panel_src()
    # The submitted list lives behind idsToSubmit + the body literal.
    assert "{ project_ids: idsToSubmit }" in src, (
        "Link body must be exactly `{ project_ids: idsToSubmit }` "
        "so the route receives the canonical array field."
    )
    # Negative lock — no scalar `project_id` body.
    assert "{ project_id: " not in src.replace("project_ids", ""), (
        "Panel must not send a scalar `project_id` body — backend "
        "rejects with a 400."
    )


def test_panel_mounted_in_contract_details_modal():
    src = _modal_src()
    assert (
        "import ContractProjectsPanel from '../sections/ContractProjectsPanel'"
        in src
    ), (
        "ContractDetailsModal must import the new panel from "
        "../sections/ContractProjectsPanel."
    )
    assert "<ContractProjectsPanel" in src, (
        "ContractDetailsModal must render the panel."
    )
    # The modal must wire onContractUpdated so the refreshed
    # contract returned by link/unlink updates other panels.
    assert "onContractUpdated={(updated) =>" in src, (
        "Detail modal must wire ContractProjectsPanel's "
        "onContractUpdated so the refreshed contract propagates."
    )


def test_panel_does_not_invent_status_gating():
    """Backend does not restrict project linking by contract.status
    or approval_status — the route checks tenant + RBAC + the edit-
    permission union. The UI must not pretend it does."""
    src = _panel_src()
    for invented_gate in (
        "contract.status === 'active'",
        "contract.status === 'completed'",
        "contract.status === 'cancelled'",
        "contract.approval_status === 'pending'",
        "contract.approval_status === 'approved'",
    ):
        assert invented_gate not in src, (
            f"Panel must not gate link/unlink on {invented_gate!r} — "
            "backend has no such restriction."
        )
