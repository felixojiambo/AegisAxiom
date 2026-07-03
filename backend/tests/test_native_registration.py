"""
Native Module Permission Registration Tests

Unit-level coverage of the @register_module_permissions decorator:
- The decorator runs the wrapped callable at import/decoration time and
  stores the returned list under module_name in the in-memory registry.
- Re-decorating the same module_name overwrites cleanly (no duplication).
- TypeError is raised when the wrapped callable returns a non-list.
- get_registered_module_permissions flattens every module's list in
  insertion order and returns the union.
- The 'core' module from routes/roles.py is registered after import and
  still contains the canonical AEGISAXIOM permissions seed list.

Run with:
    pytest backend/tests/test_native_registration.py -v
"""
import pytest

from services.permission_registry import (
    _module_registry,
    get_registered_module_permissions,
    register_module_permissions,
)


@pytest.fixture
def isolated_registry():
    """Snapshot the registry, run the test, then restore it.

    Lets tests register and overwrite without polluting the live module
    state used by routes.roles import-time registration.
    """
    snapshot = dict(_module_registry)
    try:
        yield _module_registry
    finally:
        _module_registry.clear()
        _module_registry.update(snapshot)


# ---------------------------------------------------------------------------
# Decorator behaviour
# ---------------------------------------------------------------------------

class TestRegisterModulePermissionsDecorator:
    def test_decorator_invokes_callable_at_decoration_time(self, isolated_registry):
        called = {"count": 0}

        @register_module_permissions("test_invocation")
        def perms():
            called["count"] += 1
            return [{"key": "test.x", "group": "T", "label": "X"}]

        # Decorator should have run perms() exactly once at module load.
        assert called["count"] == 1
        assert "test_invocation" in isolated_registry
        assert isolated_registry["test_invocation"] == [
            {"key": "test.x", "group": "T", "label": "X"}
        ]

    def test_decorator_returns_original_callable(self, isolated_registry):
        @register_module_permissions("test_returns_fn")
        def perms():
            return [{"key": "test.y", "group": "T", "label": "Y"}]

        assert callable(perms)
        # Calling again should return the same list shape, proving the
        # function is intact (not replaced by its return value).
        assert perms() == [{"key": "test.y", "group": "T", "label": "Y"}]

    def test_redecorating_same_module_name_overwrites(self, isolated_registry):
        @register_module_permissions("test_overwrite")
        def first():
            return [{"key": "first.k", "group": "G", "label": "First"}]

        @register_module_permissions("test_overwrite")
        def second():
            return [{"key": "second.k", "group": "G", "label": "Second"}]

        # Latest registration wins; no duplication.
        assert isolated_registry["test_overwrite"] == [
            {"key": "second.k", "group": "G", "label": "Second"}
        ]

    def test_non_list_return_raises(self, isolated_registry):
        with pytest.raises(TypeError):
            @register_module_permissions("test_bad_return")
            def perms():
                return {"key": "x"}  # dict, not a list

    def test_entry_missing_key_raises(self, isolated_registry):
        # Validation should fail at decoration time so misconfigured modules
        # fail fast on import rather than seed half-built docs into Mongo.
        with pytest.raises(ValueError):
            @register_module_permissions("test_missing_key")
            def perms():
                return [{"group": "G", "label": "No key"}]

    def test_entry_missing_group_or_label_raises(self, isolated_registry):
        with pytest.raises(ValueError):
            @register_module_permissions("test_missing_group")
            def perms():
                return [{"key": "x.y", "label": "no group"}]

        with pytest.raises(ValueError):
            @register_module_permissions("test_missing_label")
            def perms2():
                return [{"key": "x.y", "group": "X"}]

    def test_non_dict_entry_raises(self, isolated_registry):
        with pytest.raises(ValueError):
            @register_module_permissions("test_non_dict_entry")
            def perms():
                return ["not_a_dict"]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestGetRegisteredModulePermissions:
    def test_flattens_every_module_in_insertion_order(self, isolated_registry):
        _module_registry.clear()

        @register_module_permissions("alpha")
        def a():
            return [
                {"key": "a.1", "group": "A", "label": "A1"},
                {"key": "a.2", "group": "A", "label": "A2"},
            ]

        @register_module_permissions("beta")
        def b():
            return [{"key": "b.1", "group": "B", "label": "B1"}]

        flat = get_registered_module_permissions()
        assert [p["key"] for p in flat] == ["a.1", "a.2", "b.1"]

    def test_empty_registry_returns_empty_list(self, isolated_registry):
        _module_registry.clear()
        assert get_registered_module_permissions() == []


# ---------------------------------------------------------------------------
# Real-world: routes.roles registers 'core'
# ---------------------------------------------------------------------------

class TestCoreModuleRegisteredAtImport:
    def test_routes_roles_import_registers_core(self):
        """Importing routes.roles must populate the 'core' entry.

        The fixture isn't used here — we want to assert against the live
        module registry as it exists after server startup.
        """
        import routes.roles  # noqa: F401  side-effect import

        assert "core" in _module_registry, (
            "routes.roles import should have registered 'core' permissions"
        )
        core_perms = _module_registry["core"]
        keys = {p["key"] for p in core_perms}
        # Spot-check several canonical AEGISAXIOM permissions to catch silent
        # truncation of the seed list.
        for required in (
            "deals.view.all",
            "contracts.delete",
            "roles.manage",
            "finance.portal",
            "ai_employees.manage",
        ):
            assert required in keys, f"core registry missing {required!r}"

    def test_core_is_in_aggregated_output(self):
        # Force the import here too so the test passes when run in isolation
        # (otherwise it depends on a sibling test having already imported).
        import routes.roles  # noqa: F401
        flat_keys = {p["key"] for p in get_registered_module_permissions()}
        assert "deals.view.all" in flat_keys
