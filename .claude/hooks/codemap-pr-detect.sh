#!/usr/bin/env bash
# codemap-pr-detect.sh — fires after `gh pr create`, detects structural changes,
# writes .codemap-pending.json marker if any found, and prints a relay message
# back to the session suggesting /codemap-update.
#
# Style matches block-protected-branch.sh (PostToolUse, informational only).
# Exits silently when no structural changes are present.

set -u

input="$(cat)"

cmd="$(printf '%s' "$input" | python -c "import sys,json
try: d=json.load(sys.stdin)
except: sys.exit(0)
print((d.get('tool_input') or {}).get('command') or '')" 2>/dev/null)"

[ -z "$cmd" ] && exit 0

# Only run for gh pr create
printf '%s' "$cmd" | grep -qE '\bgh[[:space:]]+pr[[:space:]]+create\b' || exit 0

# Find repo root
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$repo_root" || exit 0

# Determine base branch — prefer dev, fall back to main
base="$(git merge-base HEAD origin/dev 2>/dev/null)"
if [ -z "$base" ]; then
  base="$(git merge-base HEAD origin/main 2>/dev/null)"
fi
[ -z "$base" ] && exit 0

# Changed file list
changed="$(git diff --name-only "$base"...HEAD 2>/dev/null)"
[ -z "$changed" ] && exit 0

# Structural-change detection (deterministic; no LLM)
# Patterns we count as "structural":
#   - new file added in backend/routes, backend/services, frontend/src/components
#   - existing file deleted in those paths
#   - new @router.{get,post,put,patch,delete} decorator
#   - new register_fields / register_module_* call
#   - new export default
#   - new entry in NAV_GROUPS (Layout files only)
structural=""
add_structural() {
  structural="${structural}${1}|${2}
"
}

while IFS= read -r file; do
  [ -z "$file" ] && continue

  # Deleted file
  if [ ! -f "$file" ]; then
    case "$file" in
      backend/routes/*.py|backend/services/*.py|backend/services/*/*.py)
        add_structural "$file" "deleted"
        ;;
      frontend/src/components/*.js|frontend/src/components/*.jsx|frontend/src/components/*.tsx)
        add_structural "$file" "deleted"
        ;;
    esac
    continue
  fi

  # Get the diff for this file
  diff="$(git diff "$base"...HEAD -- "$file" 2>/dev/null)"
  [ -z "$diff" ] && continue

  # New file: diff header has "new file mode"
  if printf '%s' "$diff" | head -3 | grep -q "^new file mode"; then
    case "$file" in
      backend/routes/*.py|backend/services/*.py|backend/services/*/*.py)
        add_structural "$file" "added"
        continue
        ;;
      frontend/src/components/*.js|frontend/src/components/*.jsx|frontend/src/components/*.tsx)
        add_structural "$file" "added"
        continue
        ;;
    esac
  fi

  # New route decorator
  if printf '%s' "$diff" | grep -qE '^\+[[:space:]]*@(router|api_router)\.(get|post|put|patch|delete)\b'; then
    add_structural "$file" "structural-route"
    continue
  fi

  # New field/event/module registration
  if printf '%s' "$diff" | grep -qE '^\+[[:space:]]*register_(fields|module_config|module_events)\b'; then
    add_structural "$file" "structural-registry"
    continue
  fi

  # New default export (JS/TS)
  if printf '%s' "$diff" | grep -qE '^\+[[:space:]]*export[[:space:]]+default\b'; then
    add_structural "$file" "structural-export"
    continue
  fi

  # New NAV_GROUPS entry (Layout files)
  case "$file" in
    *Layout.js|*Layout.jsx|*Layout.tsx)
      if printf '%s' "$diff" | grep -qE '^\+[[:space:]]*\{[[:space:]]*path:[[:space:]]*'; then
        add_structural "$file" "structural-nav"
      fi
      ;;
  esac
done <<< "$changed"

# Nothing structural — exit silently
[ -z "$structural" ] && exit 0

# Build JSON manifest using python (consistent with the other hook)
manifest="$(printf '%s' "$structural" | python -c "
import sys, json, datetime
entries = []
for line in sys.stdin.read().strip().splitlines():
    if not line or '|' not in line:
        continue
    path, ctype = line.split('|', 1)
    entries.append({'path': path, 'change_type': ctype})
doc = {
    'detected_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'merge_base': sys.argv[1] if len(sys.argv) > 1 else '',
    'structural_changes': entries,
}
print(json.dumps(doc, indent=2))
" "$base" 2>/dev/null)"

[ -z "$manifest" ] && exit 0

printf '%s\n' "$manifest" > .codemap-pending.json

count="$(printf '%s' "$structural" | grep -c '|' || true)"

# Print relay message — this goes back into the Claude session as PostToolUse output
cat <<EOF

═══ codemap-pr-detect ═══
$count structural change(s) detected in this PR vs $base.
CODEMAP.md may be stale. Recommended next step:

    /codemap-update

Manifest written to .codemap-pending.json. The /codemap-update skill
will pick it up automatically and clear the marker on success.
═════════════════════════
EOF

exit 0
