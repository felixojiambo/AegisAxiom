"""Convention-based permission implication engine.

Pure functions that derive permission implications from a permission key
string. No DB access, no I/O. Same-prefix only — never crosses module
boundaries.

Verb ladder:  delete > edit > view
Scope ladder: all > own

`static_implications` returns the static, convention-derived edges for a
single key. `resolve_implications` walks the closure across both static
convention AND a caller-provided registry graph (registry edges are
fetched separately so this module stays pure / I/O-free).
"""

from __future__ import annotations

from collections import deque

# Verb ladder, ordered from highest to lowest.
# A grant of a higher verb implies all lower verbs on the same prefix.
_VERB_LADDER: tuple[str, ...] = ("delete", "edit", "view")

# Scope tokens, in descending order. Membership check is also used to
# detect whether a key carries a scope suffix at all.
_SCOPES: frozenset[str] = frozenset({"all", "own"})

# Verbs that DO NOT participate in the implication ladder. Granting one
# of these never implies any other permission. Admins assign these
# explicitly because the implied audience is intentionally narrower or
# orthogonal to the read/write hierarchy.
#
# - change_status: workflow transition; not a generic write
# - assign:        ownership transfer; orthogonal to edit
# - access:        top-level app gate; coarse on/off, no ladder semantics
# - create:        creation rights are deliberately decoupled from edit
#                  (e.g., a "submit ticket" role can create without edit)
# Any verb not in _VERB_LADDER is treated as non-participating, including
# the four named above and any future custom verbs.


def static_implications(perm_key: str) -> list[str]:
    """Return convention-derived implications for a single perm key.

    Pure function. Same-prefix only — never crosses module boundaries.
    Returns an empty list for non-ladder verbs, terminal nodes, wildcards,
    and malformed keys.

    Examples:
        "crm.contacts.delete.all" -> [
            "crm.contacts.delete.own",
            "crm.contacts.edit.all",
            "crm.contacts.edit.own",
            "crm.contacts.view.all",
            "crm.contacts.view.own",
        ]
        "crm.contacts.edit"        -> ["crm.contacts.view"]
        "crm.contacts.view"        -> []
        "finance.portal"           -> []   # non-verb tail
        "crm.contacts.change_status" -> [] # special verb, not in ladder
        "crm.contacts.create"      -> []   # create is decoupled from edit
        "*"                        -> []
    """
    if not perm_key or not isinstance(perm_key, str):
        return []

    tokens = perm_key.split(".")
    if len(tokens) < 2:
        # Single-token keys ("*", "access", anything top-level) have no
        # prefix to imply against.
        return []

    last = tokens[-1]

    # Detect optional scope suffix.
    if last in _SCOPES:
        if len(tokens) < 3:
            # Need at least <prefix>.<verb>.<scope>.
            return []
        scope = last
        verb = tokens[-2]
        prefix_tokens = tokens[:-2]
    else:
        scope = None
        verb = last
        prefix_tokens = tokens[:-1]

    if not prefix_tokens:
        return []

    if verb not in _VERB_LADDER:
        # Non-ladder verb (create, change_status, assign, access, custom).
        return []

    prefix = ".".join(prefix_tokens)
    verb_index = _VERB_LADDER.index(verb)

    implied: list[str] = []

    # Same-verb scope implication: <verb>.all implies <verb>.own.
    if scope == "all":
        implied.append(f"{prefix}.{verb}.own")

    # Lower verbs on the same prefix.
    for lower_verb in _VERB_LADDER[verb_index + 1 :]:
        if scope is None:
            implied.append(f"{prefix}.{lower_verb}")
        elif scope == "all":
            implied.append(f"{prefix}.{lower_verb}.all")
            implied.append(f"{prefix}.{lower_verb}.own")
        else:  # scope == "own"
            implied.append(f"{prefix}.{lower_verb}.own")

    return implied


def resolve_implications(
    perm_keys: set[str],
    registry_graph: dict[str, list[str]] | None = None,
) -> set[str]:
    """Return the transitive closure of an input set of permission keys.

    Combines two independent sources of implication edges:
      1. Static convention via :func:`static_implications` — applied to
         every key encountered, including those reached via registry edges.
      2. Registry-declared `implies` edges supplied as ``registry_graph``
         (``{perm_key: [implied_keys, ...]}``). Pass ``None`` to skip
         registry expansion entirely (e.g., for unit tests that only
         exercise convention).

    The walk is a BFS worklist: pop a key, compute its outgoing edges
    from both sources, enqueue any not-yet-visited targets. Cycles are
    handled implicitly because already-visited keys are skipped — so the
    function remains correct even if the registry graph somehow contains
    a cycle (the DAG validator catches this at startup, but resolution
    must never wedge a request).

    Same-prefix only — never crosses module boundaries via convention.
    Registry edges are honored as declared (the registry is responsible
    for any cross-module bridging it wants to express).

    Idempotent: ``resolve_implications(resolve_implications(s)) == resolve_implications(s)``.

    Time complexity: O(N * D) where N is the number of reachable nodes
    and D is the maximum fan-out per node. Acceptable for our scale
    (low hundreds of permissions, fan-out typically ~5).

    The ``*`` wildcard is preserved if present in the input but produces
    no implications of its own — wildcard handling is the caller's
    responsibility (see ``has_perm`` short-circuit).

    Args:
        perm_keys: Initial set of permission keys held by the user.
        registry_graph: Optional adjacency map of registry-declared edges.

    Returns:
        A new set containing every key reachable from ``perm_keys`` via
        either source of edges, including the originals.
    """
    if not perm_keys:
        return set()

    closure: set[str] = set()
    queue: deque[str] = deque(perm_keys)

    while queue:
        current = queue.popleft()
        if current in closure:
            continue
        closure.add(current)

        # Static convention edges (pure, no I/O).
        for implied in static_implications(current):
            if implied not in closure:
                queue.append(implied)

        # Registry-declared edges, if provided.
        if registry_graph is not None:
            for implied in registry_graph.get(current, ()):
                if isinstance(implied, str) and implied and implied not in closure:
                    queue.append(implied)

    return closure
