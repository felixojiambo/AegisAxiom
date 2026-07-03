"""
End-to-End Ingestion Test — Davies PMO App

Exercises the full extraction → registration pipeline against the real
Davies PMO app export. Runs in-memory (no MongoDB required).

Usage:
    cd backend
    python -m pytest tests/test_ingestion_e2e.py -v

Requires: Davies PMO app cloned somewhere accessible. Set DAVIES_APP_DIR
env var or it will try common locations.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ingestion.analyzer import EmergentAnalyzer
from services.ingestion.models import (
    validate_slug, slug_to_app_slug, RESERVED_NAMESPACES,
    FieldDefinition, IngestionFieldCatalogue,
)
from services.ingestion.rbac_generator import generate_permissions, suggest_roles
from services.field_registry import (
    register_fields, register_module_events, register_module_config,
    get_fields, get_all_modules, get_entities_for_module,
    get_module_events, get_module_config,
)


# ==================== FIXTURES ====================

def find_davies_app():
    """Find the cloned Davies PMO app directory."""
    candidates = [
        os.environ.get("DAVIES_APP_DIR", ""),
        "C:/Users/harre/AppData/Local/Temp/Davies-PMO-app",
        "/tmp/Davies-PMO-app",
        os.path.expanduser("~/AppData/Local/Temp/Davies-PMO-app"),
    ]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "backend", "server.py")):
            return path
    pytest.skip("Davies PMO app not found. Set DAVIES_APP_DIR env var.")


@pytest.fixture(scope="module")
def davies_dir():
    return find_davies_app()


@pytest.fixture(scope="module")
def analyzer(davies_dir):
    a = EmergentAnalyzer(davies_dir)
    a.analyze()
    return a


@pytest.fixture(scope="module")
def summary(analyzer):
    return analyzer.get_summary()


# ==================== STAGE: VALIDATION ====================

class TestValidation:
    def test_emergent_metadata_extracted(self, summary):
        meta = summary["emergent_metadata"]
        assert meta, "Emergent metadata should be extracted"
        assert "job_id" in meta or "env_image_name" in meta

    def test_server_py_found(self, summary):
        assert summary["backend"]["server_py_exists"] is True

    def test_slug_validation(self):
        valid, _ = validate_slug("davies-pmo")
        assert valid

        valid, err = validate_slug("projects")
        assert not valid
        assert "reserved" in err.lower()

        valid, err = validate_slug("ab")
        assert not valid  # too short

        valid, err = validate_slug("-bad-slug")
        assert not valid  # leading hyphen

    def test_slug_to_app_slug(self):
        assert slug_to_app_slug("davies-pmo") == "davies_pmo"
        assert slug_to_app_slug("my-cool-app") == "my_cool_app"


# ==================== STAGE: ANALYSIS ====================

class TestAnalysis:
    def test_models_extracted(self, summary):
        assert summary["backend"]["total_models"] >= 20, \
            f"Expected 20+ models, got {summary['backend']['total_models']}"

    def test_enums_extracted(self, summary):
        assert summary["backend"]["total_enums"] >= 8, \
            f"Expected 8+ enums, got {summary['backend']['total_enums']}"

    def test_routes_extracted(self, summary):
        assert summary["backend"]["total_routes"] >= 50, \
            f"Expected 50+ routes, got {summary['backend']['total_routes']}"

    def test_frontend_pages_extracted(self, summary):
        assert summary["frontend"]["total_pages"] >= 14, \
            f"Expected 14+ pages, got {summary['frontend']['total_pages']}"


# ==================== STAGE: CLASSIFICATION ====================

class TestClassification:
    def test_infrastructure_detected(self, summary):
        infra = summary["classification"]["infrastructure"]
        infra_names = [c["name"] for c in infra]
        assert any("auth" in n.lower() for n in infra_names), \
            "Auth routes should be classified as infrastructure"
        assert any("UserCreate" in n for n in infra_names), \
            "UserCreate model should be classified as infrastructure"

    def test_app_logic_detected(self, summary):
        app_logic = summary["classification"]["app_logic"]
        assert len(app_logic) > 50, \
            f"Expected 50+ app logic components, got {len(app_logic)}"

    def test_dashboard_needs_review(self, summary):
        review = summary["classification"]["needs_review"]
        review_names = [c["name"] for c in review]
        assert "DashboardPage" in review_names, \
            "DashboardPage should be flagged for review"

    def test_login_is_infrastructure(self, summary):
        infra = summary["classification"]["infrastructure"]
        page_names = [c["name"] for c in infra if c["type"] == "page"]
        assert "LoginPage" in page_names
        assert "RegisterPage" in page_names


# ==================== STAGE: ENTITY EXTRACTION ====================

class TestEntityExtraction:
    def test_core_entities_found(self, summary):
        entities = list(summary["entities"].keys())
        expected = ["project", "sprint", "task", "wave", "meeting", "blocker"]
        for e in expected:
            assert e in entities, f"Entity '{e}' not found. Got: {entities}"

    def test_project_fields(self, analyzer):
        entity = analyzer.entities.get("project")
        assert entity, "Project entity not found"
        field_names = [f.name for f in entity["fields"]]
        assert "name" in field_names
        assert "customer_name" in field_names
        assert "budget_hours" in field_names

    def test_task_has_status_enum(self, analyzer):
        entity = analyzer.entities.get("task")
        assert entity, "Task entity not found"
        assert "TaskStatus" in entity["enums"], \
            f"TaskStatus enum not matched to task. Got: {list(entity['enums'].keys())}"

    def test_task_status_values(self, analyzer):
        entity = analyzer.entities["task"]
        values = [v["value"] for v in entity["enums"]["TaskStatus"]]
        assert "not_started" in values
        assert "working_on_it" in values
        assert "stuck" in values
        assert "done" in values

    def test_task_priority_values(self, analyzer):
        entity = analyzer.entities["task"]
        assert "TaskPriority" in entity["enums"]
        values = [v["value"] for v in entity["enums"]["TaskPriority"]]
        assert set(values) == {"low", "medium", "high", "critical"}

    def test_entity_relationships(self, analyzer):
        catalogues = analyzer.get_field_catalogues("test_app", ["task"])
        task_cat = catalogues[0]
        rel_targets = [r.to_entity for r in task_cat.relationships]
        assert "project" in rel_targets, "task.project_id should reference project"
        assert "sprint" in rel_targets, "task.sprint_id should reference sprint"


# ==================== STAGE: FIELD CATALOGUE ====================

class TestFieldCatalogue:
    def test_catalogue_generated(self, analyzer):
        selected = ["project", "task", "sprint", "wave"]
        catalogues = analyzer.get_field_catalogues("test_app", selected)
        assert len(catalogues) == 4

    def test_catalogue_has_fields(self, analyzer):
        catalogues = analyzer.get_field_catalogues("test_app", ["task"])
        task_cat = catalogues[0]
        assert len(task_cat.fields) >= 10, \
            f"Expected 10+ fields for task, got {len(task_cat.fields)}"

    def test_catalogue_has_routes(self, analyzer):
        catalogues = analyzer.get_field_catalogues("test_app", ["task"])
        task_cat = catalogues[0]
        assert len(task_cat.api_routes) >= 4, \
            f"Expected 4+ routes for task, got {len(task_cat.api_routes)}"

    def test_catalogue_route_operations(self, analyzer):
        catalogues = analyzer.get_field_catalogues("test_app", ["task"])
        task_cat = catalogues[0]
        operations = {r.operation for r in task_cat.api_routes}
        assert "create" in operations
        assert "list" in operations
        assert "update" in operations
        assert "delete" in operations

    def test_field_types_mapped(self, analyzer):
        catalogues = analyzer.get_field_catalogues("test_app", ["task"])
        task_cat = catalogues[0]
        type_map = {f.name: f.type for f in task_cat.fields}
        assert type_map.get("estimated_hours") == "number"
        assert type_map.get("title") == "string"
        assert type_map.get("dependencies") == "list"


# ==================== STAGE: FIELD REGISTRATION ====================

class TestFieldRegistration:
    def test_register_into_aegisaxiom(self, analyzer):
        app_slug = "test_davies_pmo"
        selected = ["project", "task", "sprint", "wave"]
        catalogues = analyzer.get_field_catalogues("test_reg", selected)

        for cat in catalogues:
            field_defs = [
                {"name": f.name, "type": f.type, "label": f.label, "description": f.description}
                for f in cat.fields
            ]
            if any(f.name == "status" for f in cat.fields):
                for fd in field_defs:
                    if fd["name"] == "status":
                        # Add options from enum
                        for enum in cat.enums:
                            if enum.field_name == "status":
                                fd["options"] = enum.values
            register_fields(app_slug, cat.entity_name, field_defs)
            register_module_config(app_slug, f"{app_slug}_{cat.entity_name}", f"{cat.entity_name}_id", "Custom")
            events = ["created", "updated", "deleted"]
            if any(f.name == "status" for f in cat.fields):
                events.append("status_changed")
            register_module_events(app_slug, cat.entity_name, events)

        # Verify registration
        modules = get_all_modules()
        assert app_slug in modules

        entities = get_entities_for_module(app_slug)
        assert "project" in entities
        assert "task" in entities
        assert "sprint" in entities
        assert "wave" in entities

        # Verify fields
        task_fields = get_fields(app_slug, "task")
        assert len(task_fields) >= 10
        field_names = [f["name"] for f in task_fields]
        assert "title" in field_names
        assert "status" in field_names

        # Verify events
        task_events = get_module_events(app_slug, "task")
        assert "created" in task_events
        assert "status_changed" in task_events

        # Verify module config (returns the last registered config for the slug)
        config = get_module_config(app_slug)
        assert config is not None
        assert config["collection"].startswith(app_slug)


# ==================== STAGE: RBAC GENERATION ====================

class TestRBACGeneration:
    def test_permissions_generated(self, analyzer):
        selected = ["project", "task", "sprint", "wave", "meeting", "blocker"]
        catalogues = [c.model_dump() for c in analyzer.get_field_catalogues("test", selected)]
        perms = generate_permissions("davies_pmo", "Davies PMO", catalogues)

        assert len(perms) >= 20, f"Expected 20+ permissions, got {len(perms)}"

        perm_keys = [p["key"] for p in perms]
        assert "davies_pmo.access" in perm_keys
        assert "davies_pmo.project.view" in perm_keys
        assert "davies_pmo.task.create" in perm_keys
        assert "davies_pmo.task.change_status" in perm_keys
        assert "davies_pmo.task.assign" in perm_keys

    def test_role_suggestions(self, analyzer):
        selected = ["project", "task", "sprint", "wave"]
        catalogues = [c.model_dump() for c in analyzer.get_field_catalogues("test", selected)]
        perms = generate_permissions("davies_pmo", "Davies PMO", catalogues)
        roles = suggest_roles("davies_pmo", "Davies PMO", perms,
                              ["admin", "project_manager", "engineer"])

        assert len(roles) >= 3
        role_names = [r["name"] for r in roles]
        assert "davies_pmo_admin" in role_names
        assert "davies_pmo_manager" in role_names
        assert "davies_pmo_member" in role_names
        assert "davies_pmo_viewer" in role_names

    def test_admin_has_all_permissions(self, analyzer):
        selected = ["project", "task"]
        catalogues = [c.model_dump() for c in analyzer.get_field_catalogues("test", selected)]
        perms = generate_permissions("davies_pmo", "Davies PMO", catalogues)
        roles = suggest_roles("davies_pmo", "Davies PMO", perms, ["admin"])

        admin_role = next(r for r in roles if r["name"] == "davies_pmo_admin")
        assert len(admin_role["permissions"]) == len(perms)

    def test_viewer_has_only_view_permissions(self, analyzer):
        selected = ["project", "task"]
        catalogues = [c.model_dump() for c in analyzer.get_field_catalogues("test", selected)]
        perms = generate_permissions("davies_pmo", "Davies PMO", catalogues)
        roles = suggest_roles("davies_pmo", "Davies PMO", perms, [])

        viewer_role = next(r for r in roles if r["name"] == "davies_pmo_viewer")
        for perm in viewer_role["permissions"]:
            assert ".view" in perm or perm.endswith(".access"), \
                f"Viewer should only have view/access perms, got: {perm}"


# ==================== FULL PIPELINE SUMMARY ====================

class TestFullPipeline:
    """Runs the complete extraction pipeline and produces a pass/fail report."""

    def test_full_pipeline_report(self, analyzer, summary, capsys):
        """Run everything and print a structured report."""
        app_slug = "e2e_davies_pmo"
        selected = ["project", "task", "sprint", "wave", "meeting", "blocker"]

        results = {}

        # Stage: Validation
        results["validation"] = "PASS" if summary["backend"]["server_py_exists"] else "FAIL"

        # Stage: Analysis
        results["analysis"] = "PASS" if summary["backend"]["total_models"] >= 20 else "FAIL"

        # Stage: Classification
        infra_count = len(summary["classification"]["infrastructure"])
        results["classification"] = "PASS" if infra_count >= 10 else "FAIL"

        # Stage: Entity extraction
        entity_count = len(summary["entities"])
        results["entity_extraction"] = "PASS" if entity_count >= 10 else "FAIL"

        # Stage: Field catalogue
        catalogues = analyzer.get_field_catalogues("e2e_test", selected)
        total_fields = sum(len(c.fields) for c in catalogues)
        results["field_catalogue"] = "PASS" if total_fields >= 30 else "FAIL"

        # Stage: Field registration
        for cat in catalogues:
            field_defs = [{"name": f.name, "type": f.type, "label": f.label} for f in cat.fields]
            register_fields(app_slug, cat.entity_name, field_defs)
            register_module_events(app_slug, cat.entity_name, ["created", "updated", "deleted"])

        registered_entities = get_entities_for_module(app_slug)
        results["field_registration"] = "PASS" if len(registered_entities) == len(selected) else "FAIL"

        # Stage: RBAC generation
        cat_dicts = [c.model_dump() for c in catalogues]
        perms = generate_permissions(app_slug, "Davies PMO", cat_dicts)
        roles = suggest_roles(app_slug, "Davies PMO", perms,
                              ["admin", "project_manager", "engineer"])
        results["rbac_generation"] = "PASS" if len(perms) >= 20 and len(roles) >= 3 else "FAIL"

        # Stage: Workflow events
        task_events = get_module_events(app_slug, "task")
        results["workflow_events"] = "PASS" if "created" in task_events else "FAIL"

        # Print report
        print("\n" + "=" * 60)
        print("  INGESTION E2E TEST REPORT — Davies PMO")
        print("=" * 60)
        all_pass = True
        for stage, status in results.items():
            icon = "✓" if status == "PASS" else "✗"
            print(f"  {icon} {stage:30s} {status}")
            if status != "PASS":
                all_pass = False
        print("-" * 60)
        print(f"  Entities: {entity_count} | Fields: {total_fields} | "
              f"Permissions: {len(perms)} | Roles: {len(roles)}")
        print("=" * 60)
        assert all_pass, "Not all stages passed"
