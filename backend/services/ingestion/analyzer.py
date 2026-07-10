"""
App Ingestion — Source Code Analyzer

Reads an Emergent app export and extracts:
- Pydantic models and their fields
- Enum definitions
- API routes with methods and operations
- Entity relationships
- Classification of components as infrastructure vs app logic

This module works by parsing the Python AST of server.py and scanning
the frontend file structure. It does NOT execute any of the source code.
"""
import ast
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from services.ingestion.models import (
    FieldDefinition, EnumDefinition, EntityRelationship, ApiRoute,
    ClassifiedComponent, ClassificationTier,
)

logger = logging.getLogger(__name__)

# ==================== INFRASTRUCTURE DETECTION ====================

# Backend patterns that indicate infrastructure (always discard)
INFRA_ROUTE_PATTERNS = {
    "/auth/login", "/auth/register", "/auth/logout", "/auth/me", "/auth/refresh",
    "/users", "/users/{user_id}",
}
INFRA_ROUTE_PREFIXES = {"/auth"}
INFRA_MODEL_NAMES = {"UserCreate", "UserLogin", "UserResponse"}
INFRA_ENUM_NAMES = {"UserRole"}  # the auth-level UserRole, not domain roles

# Frontend files that are always infrastructure
INFRA_FRONTEND_FILES = {
    "LoginPage", "RegisterPage", "AuthContext", "AppLayout", "Header", "Sidebar",
}
INFRA_FRONTEND_DIRS = {"components/ui", "components/layout"}

# Emergent-specific dependencies to strip
EMERGENT_BACKEND_DEPS = {"emergentintegrations"}
EMERGENT_FRONTEND_DEPS = {"@emergentbase/visual-edits"}


# ==================== PYDANTIC TYPE MAPPING ====================

PYDANTIC_TYPE_MAP = {
    "str": "string",
    "string": "string",
    "int": "number",
    "float": "number",
    "Decimal": "number",
    "bool": "boolean",
    "datetime": "date",
    "date": "date",
    "time": "time",
    "List": "list",
    "list": "list",
    "Dict": "object",
    "dict": "object",
    "Optional": None,  # unwrap the inner type
    "Any": "string",
}


def _map_pydantic_type(type_str: str) -> str:
    """Map a Pydantic type annotation string to a AEGISAXIOM field type."""
    # Handle Optional[X]
    if type_str.startswith("Optional["):
        inner = type_str[9:-1]
        return _map_pydantic_type(inner)
    # Handle List[X]
    if type_str.startswith("List[") or type_str.startswith("list["):
        return "list"
    # Handle Dict[X, Y]
    if type_str.startswith("Dict[") or type_str.startswith("dict["):
        return "object"
    # Direct lookup
    base = type_str.split("[")[0].strip()
    return PYDANTIC_TYPE_MAP.get(base, "string")


def _annotation_to_str(node) -> str:
    """Convert an AST annotation node to a string representation."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_annotation_to_str(node.value)}.{node.attr}"
    elif isinstance(node, ast.Subscript):
        base = _annotation_to_str(node.value)
        if isinstance(node.slice, ast.Tuple):
            args = ", ".join(_annotation_to_str(e) for e in node.slice.elts)
        else:
            args = _annotation_to_str(node.slice)
        return f"{base}[{args}]"
    elif isinstance(node, ast.Constant):
        return str(node.value)
    elif isinstance(node, ast.Tuple):
        return ", ".join(_annotation_to_str(e) for e in node.elts)
    return "Any"


def _label_from_name(name: str) -> str:
    """Convert a snake_case field name to a human-readable label."""
    return name.replace("_", " ").title()


# ==================== AST ANALYSIS ====================

class EmergentAnalyzer:
    """Analyzes an Emergent app export directory."""

    def __init__(self, app_dir: str):
        self.app_dir = Path(app_dir)
        self.backend_dir = self.app_dir / "backend"
        self.frontend_dir = self.app_dir / "frontend"
        self.server_py = self.backend_dir / "server.py"

        # Extracted data
        self.models: dict[str, dict] = {}  # name -> {fields, base_classes, is_create, is_update}
        self.enums: dict[str, list] = {}  # name -> [{value, label}]
        self.routes: list[dict] = []
        self.entities: dict[str, dict] = {}  # entity_name -> {collection, models, routes, enums}
        # Per-file APIRouter prefix map populated during AST walk: var name -> prefix.
        # Routes from `@<var>.get(...)` get this prefix prepended. Reset before each
        # file is parsed so prefixes don't leak across files.
        self._current_file_router_prefixes: dict[str, str] = {}
        self.frontend_pages: list[str] = []
        self.frontend_components: list[str] = []
        self.classification: list[ClassifiedComponent] = []
        self.emergent_metadata: dict = {}
        self.requirements: list[str] = []
        self.frontend_deps: dict = {}

    def analyze(self) -> dict:
        """Run full analysis. Returns a summary dict."""
        self._read_emergent_metadata()
        self._read_requirements()
        self._read_frontend_deps()
        self._parse_server_py()
        self._scan_frontend()
        self._infer_entities()
        self._classify_all()
        return self.get_summary()

    def _read_emergent_metadata(self):
        """Read .emergent/emergent.yml if present."""
        yml_path = self.app_dir / ".emergent" / "emergent.yml"
        if yml_path.exists():
            content = yml_path.read_text()
            # Simple JSON/YAML parse (Emergent uses JSON-in-yml)
            try:
                self.emergent_metadata = json.loads(content)
            except json.JSONDecodeError:
                # Try basic key-value parse
                for line in content.strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        self.emergent_metadata[k.strip().strip('"')] = v.strip().strip('"')

    def _read_requirements(self):
        """Read backend/requirements.txt."""
        req_path = self.backend_dir / "requirements.txt"
        if req_path.exists():
            self.requirements = [
                line.strip() for line in req_path.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

    def _read_frontend_deps(self):
        """Read frontend/package.json dependencies."""
        pkg_path = self.frontend_dir / "package.json"
        if pkg_path.exists():
            try:
                pkg = json.loads(pkg_path.read_text())
                self.frontend_deps = {
                    **pkg.get("dependencies", {}),
                    **pkg.get("devDependencies", {}),
                }
            except json.JSONDecodeError:
                pass

    # Backend layouts the analyzer knows how to walk. Order matters only for
    # logging; every match contributes models/enums/routes to the same shared
    # buckets, so duplicate definitions across files would be merged
    # last-write-wins (which is fine since classes have unique names).
    _MULTI_FILE_BACKEND_DIRS = (
        # Davies-PMO style: backend/app/{models,api,routes}/*.py
        "app/models",
        "app/api",
        "app/routes",
        "app/routers",
        "app/schemas",
        # Plain layout: backend/{models,routes,api}/*.py
        # Note: `routers` is the FastAPI tutorial's canonical convention
        # (https://fastapi.tiangolo.com/tutorial/bigger-applications/) — many
        # Emergent-compatible apps use it instead of `routes`.
        "models",
        "routes",
        "routers",
        "api",
        "schemas",
        # backend/src/* style
        "src/models",
        "src/routes",
        "src/routers",
        "src/api",
        "src/schemas",
    )

    def _parse_python_file(self, path: Path) -> int:
        """Parse a single .py file's AST into self.models/enums/routes.

        Returns the number of classes + functions visited (for logging /
        progress reporting). Silently skips files with syntax errors so a
        single bad file in the source tree doesn't abort the whole analysis.
        """
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"analyzer: could not read {path}: {e}")
            return 0
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            logger.warning(f"analyzer: skipping {path}: {e}")
            return 0

        # Pre-pass: scan module-level assignments for `<name> = APIRouter(...)` so
        # routes declared as `@<name>.get(...)` later in the file get their
        # router prefix prepended. The prefix map is per-file (reset every call).
        self._current_file_router_prefixes = self._extract_router_prefixes(tree)

        visited = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._process_class(node)
                visited += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_route(node)
                visited += 1
        return visited

    def _extract_router_prefixes(self, tree: ast.Module) -> dict[str, str]:
        """Find module-level `<name> = APIRouter(prefix="...")` assignments.

        Returns a map of variable name -> prefix string (empty when no prefix
        kwarg was supplied). Routers without an explicit prefix still appear
        in the map with `""`, which lets the route processor distinguish
        "this is a router variable" from "this is `app`" or an unknown name.
        """
        prefixes: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            call = node.value
            # Match APIRouter(...) or fastapi.APIRouter(...).
            func = call.func
            is_apirouter = (
                (isinstance(func, ast.Name) and func.id == "APIRouter")
                or (isinstance(func, ast.Attribute) and func.attr == "APIRouter")
            )
            if not is_apirouter:
                continue
            prefix = ""
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    prefix = kw.value.value
                    break
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
        return prefixes

    def _parse_server_py(self):
        """Parse backend/server.py + any multi-file backend directories.

        Emergent apps come in two shapes:
          1. Single file — everything in backend/server.py
          2. Multi-file — thin server.py that imports from backend/app/models,
             backend/app/api, backend/routes, etc. (Davies-PMO style)

        We always parse server.py if it exists, then walk a known set of
        sub-directories. All discovered classes/routes flow into the same
        self.models / self.enums / self.routes buckets so downstream
        _infer_entities sees the union.
        """
        files_walked: list[Path] = []

        # 1. Always try server.py first.
        if self.server_py.exists():
            count = self._parse_python_file(self.server_py)
            files_walked.append(self.server_py)
            logger.debug(f"analyzer: parsed server.py ({count} top-level nodes)")
        else:
            logger.info(
                f"analyzer: no server.py at {self.server_py} — relying on "
                f"multi-file backend layout if present"
            )

        # 1b. Walk other top-level .py files at the backend root.
        # Many Emergent apps keep their domain models in `backend/models.py`
        # (a single file, not a `models/` package). Without this pass those
        # models — and any routes wired into them — are invisible to the
        # analyzer because the sub-directory walk below only descends into
        # named sub-dirs. MATCH (Appdirect-Order-POC1) is the regression case:
        # its Session, ConnectionConfig, and Row-payload models all live in
        # `backend/models.py`.
        if self.backend_dir.exists():
            for py_file in sorted(self.backend_dir.glob("*.py")):
                if py_file in files_walked:
                    continue
                if py_file.name.startswith("_") and py_file.name != "__init__.py":
                    continue
                count = self._parse_python_file(py_file)
                files_walked.append(py_file)
                if count:
                    logger.debug(
                        f"analyzer: parsed {py_file.relative_to(self.backend_dir)} "
                        f"({count} nodes)"
                    )

        # 2. Walk known multi-file backend layouts. Each candidate directory
        # is parsed recursively so apps that nest further (e.g.,
        # backend/app/api/v1/*.py) still get picked up.
        for sub in self._MULTI_FILE_BACKEND_DIRS:
            sub_dir = self.backend_dir / sub
            if not sub_dir.exists() or not sub_dir.is_dir():
                continue
            for py_file in sub_dir.rglob("*.py"):
                if py_file.name.startswith("_") and py_file.name != "__init__.py":
                    # Skip private helpers; __init__.py may have re-exports
                    # worth parsing.
                    continue
                if py_file in files_walked:
                    continue
                count = self._parse_python_file(py_file)
                files_walked.append(py_file)
                if count:
                    logger.debug(
                        f"analyzer: parsed {py_file.relative_to(self.backend_dir)} "
                        f"({count} nodes)"
                    )

        if not files_walked:
            logger.warning(
                f"analyzer: no python files found under {self.backend_dir}. "
                f"Expected server.py or one of: "
                f"{', '.join(self._MULTI_FILE_BACKEND_DIRS)}"
            )
        else:
            logger.info(
                f"analyzer: walked {len(files_walked)} python file(s); "
                f"found {len(self.models)} model(s), {len(self.enums)} enum(s), "
                f"{len(self.routes)} route(s)"
            )

    def _process_class(self, node: ast.ClassDef):
        """Process a class definition — could be a Pydantic model or Enum."""
        bases = [_annotation_to_str(b) for b in node.bases]

        # Check if it's an Enum
        if any("Enum" in b for b in bases):
            values = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and isinstance(item.value, ast.Constant):
                            values.append({
                                "value": str(item.value.value),
                                "label": _label_from_name(str(item.value.value)),
                            })
            self.enums[node.name] = values
            return

        # Check if it's a Pydantic BaseModel
        if any("BaseModel" in b for b in bases):
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    type_str = _annotation_to_str(item.annotation) if item.annotation else "Any"
                    aegisaxiom_type = _map_pydantic_type(type_str)

                    # Check for default value
                    has_default = item.value is not None
                    is_optional = type_str.startswith("Optional") or has_default

                    fields.append({
                        "name": field_name,
                        "type_str": type_str,
                        "aegisaxiom_type": aegisaxiom_type,
                        "has_default": has_default,
                        "is_optional": is_optional,
                        "line": item.lineno,
                    })

            # Classify model type
            name = node.name
            is_create = name.endswith("Create")
            is_update = name.endswith("Update")
            is_response = name.endswith("Response")

            self.models[name] = {
                "fields": fields,
                "bases": bases,
                "is_create": is_create,
                "is_update": is_update,
                "is_response": is_response,
                "line": node.lineno,
            }

    def _process_route(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        """Process a function that might be a route handler."""
        for decorator in node.decorator_list:
            path, method, owner = self._extract_route_info(decorator)
            # An empty string path is legitimate FastAPI shorthand for "the
            # router prefix's root" (e.g. `@router.post("")` on an APIRouter
            # with prefix="/api/sessions" mounts at `/api/sessions`). We must
            # accept `path == ""` and not treat it as missing.
            if path is None or not method:
                continue
            # If the decorator's owner is a router var we recognized at the
            # top of the file (`router = APIRouter(prefix=...)`), prepend
            # that prefix. `app.<method>(...)` decorators use whatever path
            # the developer wrote verbatim — FastAPI applies no extra prefix.
            if owner and owner in self._current_file_router_prefixes:
                prefix = self._current_file_router_prefixes[owner]
                if prefix:
                    if not path:
                        path = prefix
                    else:
                        # Avoid `//` when the route path starts with `/`.
                        path = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
            self.routes.append({
                "method": method.upper(),
                "path": path,
                "function_name": node.name,
                "line": node.lineno,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })

    def _extract_route_info(self, decorator) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract (path, method, owner_var) from a route decorator.

        `owner_var` is the name of the object the decorator was called on —
        e.g., `router` in `@router.get(...)` or `app` in `@app.get(...)`.
        Callers use it to look up the file-level APIRouter prefix map.
        """
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Attribute):
                method = func.attr  # get, post, put, delete, patch
                if method in ("get", "post", "put", "delete", "patch"):
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        owner = func.value.id if isinstance(func.value, ast.Name) else None
                        return decorator.args[0].value, method, owner
        return None, None, None

    def _scan_frontend(self):
        """Scan the frontend directory for pages and components."""
        pages_dir = self.frontend_dir / "src" / "pages"
        if pages_dir.exists():
            for f in pages_dir.iterdir():
                if f.suffix in (".js", ".jsx", ".tsx") and not f.name.startswith("."):
                    name = f.stem
                    self.frontend_pages.append(name)

        # Custom components (not in ui/ or layout/)
        components_dir = self.frontend_dir / "src" / "components"
        if components_dir.exists():
            for f in components_dir.rglob("*"):
                if f.suffix in (".js", ".jsx", ".tsx") and not f.name.startswith("."):
                    rel = f.relative_to(components_dir)
                    rel_str = str(rel).replace("\\", "/")
                    # Skip ui/ and layout/ directories
                    if not any(rel_str.startswith(d) for d in INFRA_FRONTEND_DIRS):
                        self.frontend_components.append(rel_str)

    def _infer_entities(self):
        """Infer domain entities from models, routes, and pages."""
        # Group models by base entity name (strip Create/Update/Response suffix)
        entity_models = {}
        for name, model in self.models.items():
            base = name
            for suffix in ("Create", "Update", "Response"):
                if name.endswith(suffix):
                    base = name[:-len(suffix)]
                    break
            entity_models.setdefault(base, []).append(name)

        # Match routes to entities by path patterns
        for entity_base, model_names in entity_models.items():
            if entity_base in INFRA_MODEL_NAMES or entity_base in ("User",):
                continue

            # Find the collection name from routes
            entity_lower = self._camel_to_snake(entity_base)
            entity_plural = self._pluralize(entity_lower)

            # Match routes — check the entity's plural form and also any
            # shortened form (e.g., value_thread → /threads, execution_node → /nodes).
            # URL slugs may use hyphens where the entity name uses underscores
            # (e.g., `provider_template` → `/api/provider-templates`), so we
            # check both spellings on every candidate segment.
            entity_parts = entity_lower.split("_")
            last_part_plural = self._pluralize(entity_parts[-1])
            entity_plural_hyphen = entity_plural.replace("_", "-")
            entity_lower_hyphen = entity_lower.replace("_", "-")
            matching_routes = [
                r for r in self.routes
                if f"/{entity_plural}" in r["path"]
                or f"/{entity_plural_hyphen}" in r["path"]
                or f"/{entity_lower}" in r["path"]
                or f"/{entity_lower_hyphen}" in r["path"]
                or (len(entity_parts) > 1 and f"/{last_part_plural}" in r["path"])
            ]

            # Find matching enums — only match if the enum name contains
            # the entity name as a distinct segment (e.g., TaskStatus matches
            # task, but SprintStatus does not match task)
            matching_enums = {}
            for enum_name, values in self.enums.items():
                if enum_name in INFRA_ENUM_NAMES:
                    continue
                enum_lower = self._camel_to_snake(enum_name)
                enum_segments = enum_lower.split("_")
                # Match if entity name appears as a prefix segment of the enum name
                if enum_lower.startswith(entity_lower + "_") or enum_lower == entity_lower:
                    matching_enums[enum_name] = values

            self.entities[entity_lower] = {
                "class_name": entity_base,
                "collection": entity_plural,
                "models": model_names,
                "routes": matching_routes,
                "enums": matching_enums,
                "fields": self._get_entity_fields(entity_base, model_names),
            }

        # Also detect entities from standalone enums that weren't matched
        for enum_name, values in self.enums.items():
            if enum_name in INFRA_ENUM_NAMES:
                continue
            # Check if this enum's entity root is already captured
            enum_entity = self._camel_to_snake(enum_name).replace("_status", "").replace("_type", "").replace("_priority", "")
            found = False
            for entity_name in self.entities:
                if enum_entity in entity_name or entity_name in enum_entity:
                    # Add to existing entity's enums
                    self.entities[entity_name]["enums"][enum_name] = values
                    found = True
                    break
            # Don't create phantom entities from standalone enums

    def _get_entity_fields(self, entity_base: str, model_names: list) -> list[FieldDefinition]:
        """Extract field definitions for an entity, preferring the Create model."""
        # Prefer Create model (has required fields), fall back to base or Update
        preferred = None
        for suffix in ("Create", "", "Update"):
            candidate = entity_base + suffix
            if candidate in self.models:
                preferred = candidate
                break

        if not preferred:
            return []

        model = self.models[preferred]
        fields = []
        for f in model["fields"]:
            # Check if this field has enum options
            options = None
            for enum_name, values in self.enums.items():
                enum_snake = self._camel_to_snake(enum_name)
                if f["name"] in enum_snake or enum_snake.endswith(f"_{f['name']}"):
                    options = values
                    break

            fields.append(FieldDefinition(
                name=f["name"],
                type=f["aegisaxiom_type"],
                label=_label_from_name(f["name"]),
                description=f"The {_label_from_name(f['name']).lower()} of the {entity_base.lower()}",
                computed=False,
                options=options,
                source_field_name=f["name"],
            ))

        return fields

    def _classify_all(self):
        """Classify all discovered components into infrastructure/app_logic/needs_review."""
        # Classify backend routes
        for route in self.routes:
            path = route["path"]
            is_infra = (
                any(path == p for p in INFRA_ROUTE_PATTERNS)
                or any(path.startswith(prefix) for prefix in INFRA_ROUTE_PREFIXES)
            )
            self.classification.append(ClassifiedComponent(
                name=f"{route['method']} {path}",
                type="route",
                tier=ClassificationTier.INFRASTRUCTURE if is_infra else ClassificationTier.APP_LOGIC,
                reason="Auth/user management route" if is_infra else "Domain API route",
                source_file="backend/server.py",
                source_lines=str(route["line"]),
            ))

        # Classify models
        for name, model in self.models.items():
            is_infra = name in INFRA_MODEL_NAMES or (
                name in ("User",) and not any(
                    name in e.get("models", []) for e in self.entities.values()
                )
            )
            self.classification.append(ClassifiedComponent(
                name=name,
                type="model",
                tier=ClassificationTier.INFRASTRUCTURE if is_infra else ClassificationTier.APP_LOGIC,
                reason="Auth model" if is_infra else "Domain model",
                source_file="backend/server.py",
                source_lines=str(model["line"]),
            ))

        # Classify enums
        for name, values in self.enums.items():
            is_infra = name in INFRA_ENUM_NAMES
            self.classification.append(ClassifiedComponent(
                name=name,
                type="enum",
                tier=ClassificationTier.INFRASTRUCTURE if is_infra else ClassificationTier.APP_LOGIC,
                reason="Auth enum" if is_infra else "Domain enum",
                source_file="backend/server.py",
            ))

        # Classify frontend pages
        for page in self.frontend_pages:
            is_infra = any(page.startswith(inf) or page == inf for inf in INFRA_FRONTEND_FILES)
            # Dashboard and notification pages need review
            needs_review = not is_infra and any(
                kw in page.lower() for kw in ("dashboard", "notification", "settings")
            )
            tier = (
                ClassificationTier.INFRASTRUCTURE if is_infra
                else ClassificationTier.NEEDS_REVIEW if needs_review
                else ClassificationTier.APP_LOGIC
            )
            reason = (
                "Auth/layout page" if is_infra
                else "May overlap with existing AEGISAXIOM feature" if needs_review
                else "Domain page"
            )
            self.classification.append(ClassifiedComponent(
                name=page,
                type="page",
                tier=tier,
                reason=reason,
                source_file=f"frontend/src/pages/{page}.js",
            ))

        # Classify dependencies
        for dep in self.requirements:
            dep_name = dep.split(">=")[0].split("==")[0].split(">")[0].split("<")[0].strip()
            if dep_name in EMERGENT_BACKEND_DEPS:
                self.classification.append(ClassifiedComponent(
                    name=dep_name,
                    type="dependency",
                    tier=ClassificationTier.INFRASTRUCTURE,
                    reason="Emergent-specific dependency",
                    source_file="backend/requirements.txt",
                ))

        for dep_name in self.frontend_deps:
            if dep_name in EMERGENT_FRONTEND_DEPS:
                self.classification.append(ClassifiedComponent(
                    name=dep_name,
                    type="dependency",
                    tier=ClassificationTier.INFRASTRUCTURE,
                    reason="Emergent-specific dependency",
                    source_file="frontend/package.json",
                ))

    def get_summary(self) -> dict:
        """Return a structured summary of the analysis."""
        infra = [c for c in self.classification if c.tier == ClassificationTier.INFRASTRUCTURE]
        app_logic = [c for c in self.classification if c.tier == ClassificationTier.APP_LOGIC]
        needs_review = [c for c in self.classification if c.tier == ClassificationTier.NEEDS_REVIEW]

        return {
            "emergent_metadata": self.emergent_metadata,
            "backend": {
                "server_py_exists": self.server_py.exists(),
                "total_models": len(self.models),
                "total_enums": len(self.enums),
                "total_routes": len(self.routes),
            },
            "frontend": {
                "total_pages": len(self.frontend_pages),
                "total_custom_components": len(self.frontend_components),
                "pages": self.frontend_pages,
                "custom_components": self.frontend_components,
            },
            "entities": {
                name: {
                    "class_name": info["class_name"],
                    "collection": info["collection"],
                    "model_count": len(info["models"]),
                    "route_count": len(info["routes"]),
                    "field_count": len(info["fields"]),
                    "enum_count": len(info["enums"]),
                }
                for name, info in self.entities.items()
            },
            "classification": {
                "infrastructure": [c.model_dump() for c in infra],
                "app_logic": [c.model_dump() for c in app_logic],
                "needs_review": [c.model_dump() for c in needs_review],
            },
            "dependencies": {
                "backend": self.requirements,
                "frontend_count": len(self.frontend_deps),
                "emergent_specific": [
                    dep for dep in self.requirements
                    if dep.split(">=")[0].split("==")[0].strip() in EMERGENT_BACKEND_DEPS
                ] + [
                    dep for dep in self.frontend_deps
                    if dep in EMERGENT_FRONTEND_DEPS
                ],
            },
        }

    def get_field_catalogues(self, app_id: str, selected_entities: list[str] = None) -> list:
        """Generate IngestionFieldCatalogue documents for selected entities."""
        from services.ingestion.models import IngestionFieldCatalogue

        catalogues = []
        for entity_name, info in self.entities.items():
            if selected_entities and entity_name not in selected_entities:
                continue

            # Build enum definitions
            enum_defs = []
            for enum_name, values in info["enums"].items():
                # Find which field uses this enum
                enum_snake = self._camel_to_snake(enum_name)
                field_name = "status"  # default
                for f in info["fields"]:
                    if f.name in enum_snake or enum_snake.endswith(f"_{f.name}"):
                        field_name = f.name
                        break
                enum_defs.append(EnumDefinition(
                    name=enum_snake,
                    entity=entity_name,
                    field_name=field_name,
                    values=values,
                    source_class_name=enum_name,
                ))

            # Build relationships
            relationships = []
            for f in info["fields"]:
                if f.name.endswith("_id") and f.name != f"{entity_name}_id":
                    ref_entity = f.name.replace("_id", "")
                    if ref_entity in self.entities:
                        relationships.append(EntityRelationship(
                            from_entity=entity_name,
                            from_field=f.name,
                            to_entity=ref_entity,
                            to_field="_id",
                            cardinality="many_to_one",
                        ))

            # Build API routes
            api_routes = []
            for r in info["routes"]:
                operation = "custom"
                if r["method"] == "GET" and "{" not in r["path"]:
                    operation = "list"
                elif r["method"] == "GET":
                    operation = "get"
                elif r["method"] == "POST":
                    operation = "create"
                elif r["method"] == "PUT" or r["method"] == "PATCH":
                    operation = "update"
                elif r["method"] == "DELETE":
                    operation = "delete"

                api_routes.append(ApiRoute(
                    method=r["method"],
                    path=r["path"],
                    operation=operation,
                    entity=entity_name,
                ))

            catalogues.append(IngestionFieldCatalogue(
                app_id=app_id,
                module_name=entity_name,
                entity_name=entity_name,
                fields=info["fields"],
                enums=enum_defs,
                relationships=relationships,
                api_routes=api_routes,
            ))

        return catalogues

    # ==================== HELPERS ====================

    @staticmethod
    def _camel_to_snake(name: str) -> str:
        """Convert CamelCase to snake_case."""
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    @staticmethod
    def _pluralize(word: str) -> str:
        """Simple English pluralization."""
        if word.endswith("y") and word[-2] not in "aeiou":
            return word[:-1] + "ies"
        if word.endswith(("s", "sh", "ch", "x", "z")):
            return word + "es"
        return word + "s"
