"""Source-level wiring contract for ContractProjectsPanel after the
PART 8 fix.

The panel must:

  1. Combine contract.project_ids[] (forward list) with
     contract.projects[].project_id (back-edge enrichment from
     project.contract_id) into one render list. Without the merge,
     imported Zoho contracts (which have project_ids=[] but 101
     projects pointing at them) showed "no projects linked."
  2. Make linked-project rows clickable so users land on the
     canonical project detail at /projects/projects/{project_id}.
  3. Hide the unlink trash icon for back-edge-only ids — the
     unlink form cannot remove them because they are not in
     contract.project_ids[].
  4. Surface an "imported link" tag on back-edge-only rows so
     operators can tell why the unlink action is missing.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "contracts" / "sections"
        / "ContractProjectsPanel.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_panel_merges_forward_and_back_edge():
    src = _component_src()
    assert "contract?.projects" in src, (
        "ContractProjectsPanel must consume contract.projects[] from "
        "the backend's back-edge enrichment, not only project_ids[]."
    )
    assert "contract?.project_ids" in src
    # Dedup + stable order.
    assert "const seen = new Set();" in src


def test_panel_navigates_to_project_detail_on_click():
    src = _component_src()
    assert "useNavigate" in src
    # The canonical project detail URL is /projects/projects/<id> —
    # ProjectsPortal parses that path and opens the detail view.
    assert "navigate(`/projects/projects/${pid}`)" in src
    # The row exposes a test id for the open action so end-to-end
    # smoke tests can target it.
    assert "data-testid={`contract-projects-open-${pid}`}" in src


def test_panel_does_not_misuse_external_project_id_for_navigation():
    """Imported projects carry both project_id (AEGISAXIOM) and
    external_project_id (Zoho Record Id). The route at
    /projects/projects/{id} expects the AEGISAXIOM project_id; using the
    Zoho external id would 404 every imported project link."""
    src = _component_src()
    assert "navigate(`/projects/projects/${pid}`)" in src
    assert "external_project_id" not in src, (
        "The navigation handler must use the AEGISAXIOM project_id, not "
        "the Zoho external_project_id."
    )


def test_panel_hides_unlink_for_back_edge_only_ids():
    src = _component_src()
    # Back-edge-only id set is computed and the trash icon is gated
    # on `!isBackEdgeOnly`.
    assert "backEdgeOnlyIds" in src
    assert "isBackEdgeOnly" in src
    assert "canEdit && !isBackEdgeOnly" in src


def test_panel_labels_back_edge_only_rows_as_imported_link():
    """Operator-facing affordance: imported back-edge linkage shows
    a small "(imported link)" tag so users understand why unlink is
    unavailable on those rows."""
    src = _component_src()
    assert "(imported link)" in src
    assert "contract-projects-backedge-" in src


def test_panel_seeds_project_docs_from_backend_enrichment():
    """The backend already returns contract.projects[] with name/
    status fields. The panel must use those instead of immediately
    re-fetching each id via /api/projects/projects/{id}."""
    src = _component_src()
    assert "initialProjectDocs" in src
    assert "useState(initialProjectDocs)" in src
