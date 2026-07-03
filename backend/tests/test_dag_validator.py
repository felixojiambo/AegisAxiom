"""
Tests for validate_dag() in services/permission_registry.py (Wave 1B).

validate_dag() calls get_implication_graph(db=db) internally, so we mock
the db argument's permissions collection to control the graph structure.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.permission_registry import validate_dag


def _make_db(nodes: dict[str, list[str]]):
    """Build a mock db whose permissions collection returns `nodes`.

    nodes: {perm_key: [implied_key, ...]}
    """
    docs = [
        {"key": k, "implies": v} for k, v in nodes.items()
    ]

    async def _async_to_list(length=None):
        return docs

    cursor = MagicMock()
    cursor.to_list = _async_to_list
    cursor.__aiter__ = lambda self: self
    # Make cursor async-iterable
    cursor.__anext__ = _make_anext(iter(docs))

    collection = MagicMock()
    collection.find = MagicMock(return_value=_make_async_cursor(docs))

    db = SimpleNamespace(permissions=collection)
    return db


def _make_async_cursor(docs):
    """Return an async-iterable mock that yields each doc."""
    class AsyncCursor:
        def __init__(self, docs):
            self._iter = iter(docs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    return AsyncCursor(docs)


def _make_anext(it):
    async def __anext__(self):
        try:
            return next(it)
        except StopIteration:
            raise StopAsyncIteration
    return __anext__


class TestValidateDagAcyclic:
    @pytest.mark.asyncio
    async def test_empty_graph_is_valid(self):
        db = _make_db({})
        await validate_dag(db=db)  # must not raise

    @pytest.mark.asyncio
    async def test_linear_chain_is_valid(self):
        # A -> B -> C — no cycles
        db = _make_db({
            "A": ["B"],
            "B": ["C"],
            "C": [],
        })
        await validate_dag(db=db)  # must not raise

    @pytest.mark.asyncio
    async def test_forest_of_trees_is_valid(self):
        # Two independent trees
        db = _make_db({
            "root1": ["child1a", "child1b"],
            "child1a": [],
            "child1b": [],
            "root2": ["child2a"],
            "child2a": [],
        })
        await validate_dag(db=db)  # must not raise

    @pytest.mark.asyncio
    async def test_terminal_edges_to_unregistered_keys_are_valid(self):
        """Edges to keys not in the registry are terminal — validator skips them cleanly."""
        db = _make_db({
            "a.view": ["missing_key_xyz"],
        })
        await validate_dag(db=db)  # must not raise


class TestValidateDagCycleDetection:
    @pytest.mark.asyncio
    async def test_self_loop_raises_runtime_error(self):
        db = _make_db({
            "A": ["A"],
        })
        with pytest.raises(RuntimeError) as exc_info:
            await validate_dag(db=db)
        assert "cycle" in str(exc_info.value).lower()
        assert "A" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_two_cycle_raises_runtime_error(self):
        db = _make_db({
            "A": ["B"],
            "B": ["A"],
        })
        with pytest.raises(RuntimeError) as exc_info:
            await validate_dag(db=db)
        err = str(exc_info.value)
        assert "cycle" in err.lower()
        # Both nodes must appear in the cycle path
        assert "A" in err
        assert "B" in err

    @pytest.mark.asyncio
    async def test_three_cycle_raises_runtime_error(self):
        db = _make_db({
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],
        })
        with pytest.raises(RuntimeError) as exc_info:
            await validate_dag(db=db)
        err = str(exc_info.value)
        assert "cycle" in err.lower()

    @pytest.mark.asyncio
    async def test_cycle_message_contains_path(self):
        """The error message must describe the cycle path (e.g., 'A -> B -> A')."""
        db = _make_db({
            "X": ["Y"],
            "Y": ["X"],
        })
        with pytest.raises(RuntimeError) as exc_info:
            await validate_dag(db=db)
        err = str(exc_info.value)
        # Path must mention both nodes and use arrow notation
        assert "->" in err
        assert "X" in err
        assert "Y" in err
