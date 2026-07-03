/**
 * Compute which sidebar nav item should be marked active for a
 * given pathname.
 *
 * Longest-match rule: among all nav items whose `path` matches the
 * pathname, the one with the LONGEST path wins. This stops a
 * generic parent route like `/contracts` from staying active when
 * a more specific sibling like `/contracts/approval-queue` is
 * open. It also keeps `/projects` highlighted when the user is at
 * `/projects/projects` because there is no longer sibling to win.
 *
 * "Matches" means one of:
 *   1. exact equality (pathname === path)
 *   2. pathname starts with `path + '/'` (deeper child route)
 *   3. pathname starts with `path + '?'` (root with query string)
 *
 * Returns the winning item's path string, or null if nothing
 * matches the current pathname.
 *
 * Pure function — no React, no router hooks. Use from the Layout
 * component AND from tests.
 */
export function computeActiveNavPath(items, pathname) {
  if (!pathname || !Array.isArray(items)) return null;
  // Normalise: drop a trailing slash so `/contracts/` matches the
  // same item as `/contracts`. Keep root `/` as itself.
  const p =
    pathname.length > 1 && pathname.endsWith('/')
      ? pathname.slice(0, -1)
      : pathname;
  let best = null;
  let bestLen = -1;
  for (const item of items) {
    if (!item || typeof item.path !== 'string' || item.path.length === 0) {
      continue;
    }
    const path = item.path;
    const matches =
      p === path || p.startsWith(path + '/') || p.startsWith(path + '?');
    if (matches && path.length > bestLen) {
      best = path;
      bestLen = path.length;
    }
  }
  return best;
}

/**
 * Convenience helper for the Layout: given the grouped nav
 * structure (the shape SidebarContent receives — an array of
 * `{ group, items }`), return the id of the group that contains
 * the active item, or null. Used to auto-expand the group whose
 * child the user is currently on.
 */
export function findActiveGroupId(filteredGroups, activePath) {
  if (!activePath || !Array.isArray(filteredGroups)) return null;
  for (const g of filteredGroups) {
    if (!g || !Array.isArray(g.items)) continue;
    if (g.items.some((it) => it && it.path === activePath)) {
      return g.group ? g.group.id : null;
    }
  }
  return null;
}
