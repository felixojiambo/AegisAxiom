#!/usr/bin/env bash
# AegisAxiom self-serve verification harness (Linux / macOS / CI — bash twin of
# scripts/verify.ps1). Same profiles, same output grammar, same exit codes.
#
#   --alembic              upgrade head -> downgrade base -> upgrade head
#   --grep <patternfile>   zero-grep over live backend code (repeatable)
#   --tests <path...>      pytest; SKIPPED PG TESTS ARE A FAILURE
#   --boot                 force-recreate backend, wait healthy, scan startup log
#   --smoke                login + health + me + one migrated + one Mongo endpoint
#   --contracts <dir>      capture_contracts.py compare against stored captures
#   --all                  all of the above, in that order
#   --keep                 leave the test DB up (only tears down what it started)
#   --native               run python on THIS host instead of in a container.
#                          CI uses this: the runner already has the deps and
#                          reaches the service database directly. Requires
#                          TEST_DATABASE_URL. --boot/--smoke/--contracts need
#                          Docker and are refused in native mode.
#
# PowerShell-style single-dash flags (-Alembic, -Grep, -Tests, -All, ...) are
# accepted too, so one command line works verbatim on both platforms.
#
# Exit code is 0 ONLY when everything requested passed.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_MAIN="$REPO_ROOT/docker-compose.yml"
COMPOSE_TEST="$REPO_ROOT/docker-compose.test.yml"

# docker-compose.test.yml runs under ITS OWN compose project — not the main
# stack's implicit (directory-name-derived) project — so `up`/`down` on either
# compose file never sees the other's containers as orphans and the two
# stacks stop fighting over who owns the default network's config hash.
# $COMPOSE_MAIN keeps Compose's inferred project name unchanged (no -p
# anywhere for it); only the TEST stack's project is renamed.
MAIN_PROJECT_NAME="$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
TEST_PROJECT_NAME="${MAIN_PROJECT_NAME}-test"
# The main stack's implicit default network (created the first time anything
# under $COMPOSE_MAIN starts). ensure_test_db() bridges postgres-test onto it
# below so `docker compose -f $COMPOSE_MAIN run backend` can still resolve
# `postgres-test` by hostname even though the two stacks are now different
# projects.
MAIN_NETWORK_NAME="${MAIN_PROJECT_NAME}_default"

# In-container view: reachable by hostname because ensure_test_db() explicitly
# bridges postgres-test onto $MAIN_NETWORK_NAME.
TEST_DB_URL_CONTAINER="${VERIFY_TEST_DATABASE_URL:-postgresql+asyncpg://aegis:aegis@postgres-test:5432/aegis_test}"
# Native/host view (published port), overridable by the caller / CI.
TEST_DB_URL_NATIVE="${TEST_DATABASE_URL:-postgresql+asyncpg://aegis:aegis@localhost:5544/aegis_test}"

# Live backend code, per phase2a-census §1.8: these are top-level directories
# under backend/. __pycache__ is excluded separately, at any depth.
GREP_EXCLUDE_DIRS=(tests scripts migrations archive)

BASE_URL="${VERIFY_BASE_URL:-http://localhost:8001}"
SMOKE_MIGRATED="${VERIFY_SMOKE_MIGRATED:-/api/tenants}"
SMOKE_LEGACY="${VERIFY_SMOKE_LEGACY:-/api/contracts}"
BOOT_TIMEOUT="${VERIFY_BOOT_TIMEOUT:-240}"
DEFAULT_TESTS="tests/test_platform_core_*_pg.py"
DEFAULT_CAPTURES="contracts/captures"

RUN_ALEMBIC=0; RUN_GREP=0; RUN_TESTS=0; RUN_BOOT=0; RUN_SMOKE=0; RUN_CONTRACTS=0
RUN_ALL=0; KEEP=0; NATIVE=0
GREP_FILES=(); TEST_SPECS=(); CAPTURE_DIR=""
STARTED_TEST_DB=0
declare -A RESULTS

say()    { printf '%s\n' "$*"; }
detail() { printf '    %s\n' "$*"; }

# Absolute on POSIX (/x) and on Windows shells (C:/x, C:\x) alike.
is_abs() { case "$1" in /*|[A-Za-z]:[/\\]*) return 0 ;; *) return 1 ;; esac; }

set_result() { # name ok info
  local name="$1" ok="$2" info="${3:-}"
  if [ "$ok" -eq 0 ]; then RESULTS[$name]=PASS; else RESULTS[$name]=FAIL; fi
  if [ -n "$info" ]; then say "[$name] ${RESULTS[$name]} $info"; else say "[$name] ${RESULTS[$name]}"; fi
  return "$ok"
}

elide() { # never print a full address
  case "$1" in
    *@*) printf '%s***@%s***\n' "${1:0:1}" "$(printf '%s' "${1#*@}" | cut -c1)" ;;
    *)   printf '<unset>\n' ;;
  esac
}

# --------------------------------------------------------------------------
# Argument parsing (accepts --flag and -Flag)
# --------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  key="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$key" in
    --alembic|-alembic)     RUN_ALEMBIC=1; shift ;;
    --boot|-boot)           RUN_BOOT=1; shift ;;
    --smoke|-smoke)         RUN_SMOKE=1; shift ;;
    --all|-all)             RUN_ALL=1; shift ;;
    --keep|-keep)           KEEP=1; shift ;;
    --native|-native)       NATIVE=1; shift ;;
    --grep|-grep)           RUN_GREP=1; shift; GREP_FILES+=("$1"); shift ;;
    --contracts|-contracts) RUN_CONTRACTS=1; shift; CAPTURE_DIR="$1"; shift ;;
    --tests|-tests)
      RUN_TESTS=1; shift
      # consume every following non-flag token as a test path
      while [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; do TEST_SPECS+=("$1"); shift; done ;;
    -h|--help|-help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) say "[verify] FAIL reason=unknown-argument arg=$1"; exit 2 ;;
  esac
done

if [ "$RUN_ALL" -eq 1 ]; then
  RUN_ALEMBIC=1; RUN_GREP=1; RUN_TESTS=1; RUN_BOOT=1; RUN_SMOKE=1; RUN_CONTRACTS=1
fi
if [ "$RUN_GREP" -eq 1 ] && [ ${#GREP_FILES[@]} -eq 0 ]; then
  while IFS= read -r f; do GREP_FILES+=("${f#"$REPO_ROOT"/}"); done \
    < <(find "$REPO_ROOT/scripts/greps" -name '*.txt' -type f 2>/dev/null | sort)
fi
[ "$RUN_TESTS" -eq 1 ]     && [ ${#TEST_SPECS[@]} -eq 0 ] && TEST_SPECS=("$DEFAULT_TESTS")
[ "$RUN_CONTRACTS" -eq 1 ] && [ -z "$CAPTURE_DIR" ]       && CAPTURE_DIR="$DEFAULT_CAPTURES"

SELECTED=()
[ "$RUN_ALEMBIC" -eq 1 ]   && SELECTED+=(alembic)
[ "$RUN_GREP" -eq 1 ]      && SELECTED+=(grep)
[ "$RUN_TESTS" -eq 1 ]     && SELECTED+=(tests)
[ "$RUN_BOOT" -eq 1 ]      && SELECTED+=(boot)
[ "$RUN_SMOKE" -eq 1 ]     && SELECTED+=(smoke)
[ "$RUN_CONTRACTS" -eq 1 ] && SELECTED+=(contracts)

if [ ${#SELECTED[@]} -eq 0 ]; then
  say '[verify] FAIL reason=no-profile-selected'
  say '[verify] usage: verify.sh [--alembic] [--grep <file>] [--tests <path...>] [--boot] [--smoke] [--contracts <dir>] [--all] [--keep] [--native]'
  exit 2
fi

if [ "$NATIVE" -eq 1 ] && { [ "$RUN_BOOT" -eq 1 ] || [ "$RUN_SMOKE" -eq 1 ] || [ "$RUN_CONTRACTS" -eq 1 ]; }; then
  say '[verify] FAIL reason=boot/smoke/contracts-require-docker (drop --native or drop those profiles)'
  exit 2
fi

say "[verify] START profiles=$(IFS=,; echo "${SELECTED[*]}") repo=$REPO_ROOT native=$NATIVE"

NEEDS_DOCKER=0
{ [ "$RUN_BOOT" -eq 1 ] || [ "$RUN_SMOKE" -eq 1 ] || [ "$RUN_CONTRACTS" -eq 1 ]; } && NEEDS_DOCKER=1
[ "$NATIVE" -eq 0 ] && { [ "$RUN_ALEMBIC" -eq 1 ] || [ "$RUN_TESTS" -eq 1 ]; } && NEEDS_DOCKER=1
if [ "$NEEDS_DOCKER" -eq 1 ] && ! docker info >/dev/null 2>&1; then
  say '[verify] FAIL reason=docker-daemon-unreachable'
  say '[verify] RESULT=FAIL'
  exit 1
fi

# --------------------------------------------------------------------------
# Docker helpers. NEVER pass --remove-orphans anywhere in this script.
# --------------------------------------------------------------------------
container_state() {
  local id="$1"
  [ -z "$id" ] && { echo missing; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing
}

wait_state() { # id accept timeout
  local id="$1" accept="$2" timeout="$3" waited=0 s
  while [ "$waited" -lt "$timeout" ]; do
    s="$(container_state "$id")"
    [ "$s" = "$accept" ] && return 0
    { [ "$s" = exited ] || [ "$s" = dead ]; } && return 1
    sleep 2; waited=$((waited + 2))
  done
  return 1
}

ensure_test_db() {
  [ "$NATIVE" -eq 1 ] && return 0   # CI supplies TEST_DATABASE_URL itself
  local id state
  id="$(docker compose -p "$TEST_PROJECT_NAME" -f "$COMPOSE_TEST" ps -q postgres-test 2>/dev/null | head -1)"
  state="$(container_state "$id")"
  if [ "$state" != healthy ] && [ "$state" != running ]; then
    detail "test db: bringing up docker-compose.test.yml (project=$TEST_PROJECT_NAME)"
    docker compose -p "$TEST_PROJECT_NAME" -f "$COMPOSE_TEST" up -d >/dev/null || return 1
    STARTED_TEST_DB=1
    id="$(docker compose -p "$TEST_PROJECT_NAME" -f "$COMPOSE_TEST" ps -q postgres-test 2>/dev/null | head -1)"
  fi
  wait_state "$id" healthy 120 || { detail 'test db: never became healthy'; return 1; }

  # Bridge: the test stack is its own project now, so postgres-test lands on
  # its own private default network. $COMPOSE_MAIN containers (e.g. `docker
  # compose run backend` during --alembic/--tests) still need to resolve
  # `postgres-test` by hostname, so connect it onto the main project's default
  # network under that alias. Create the network if $COMPOSE_MAIN hasn't
  # started anything yet this session; ignore "already connected" — both are
  # expected steady states, not failures.
  docker network inspect "$MAIN_NETWORK_NAME" >/dev/null 2>&1 \
    || docker network create "$MAIN_NETWORK_NAME" >/dev/null 2>&1
  docker network connect --alias postgres-test "$MAIN_NETWORK_NAME" "$id" >/dev/null 2>&1

  return 0
}

teardown_test_db() {
  if [ "$STARTED_TEST_DB" -eq 1 ] && [ "$KEEP" -eq 0 ]; then
    detail "test db: tearing down (started by this run; pass --keep to leave it up; project=$TEST_PROJECT_NAME)"
    docker compose -p "$TEST_PROJECT_NAME" -f "$COMPOSE_TEST" down -v >/dev/null 2>&1
  fi
}
trap teardown_test_db EXIT

# Run a shell command against the backend. In container mode this is a throwaway
# `compose run` container that inherits backend/.env (so ADMIN_* arrive without
# ever touching a command line). In native mode it runs right here.
in_backend() { # command [extra docker args...]
  local cmd="$1"; shift
  if [ "$NATIVE" -eq 1 ]; then
    ( cd "$REPO_ROOT/backend" && sh -c "$cmd" )
  else
    docker compose -f "$COMPOSE_MAIN" run --rm -T "$@" --entrypoint sh backend -c "$cmd"
  fi
}

# --------------------------------------------------------------------------
# PROFILE: alembic
# --------------------------------------------------------------------------
profile_alembic() {
  say '[alembic] START round-trip=upgrade-head,downgrade-base,upgrade-head'
  ensure_test_db || { set_result alembic 1 'reason=test-db-unavailable'; return 1; }

  local cmd out code
  # Alembic logs to stderr; fold it in so the round-trip is visible and greppable.
  cmd='{ alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic current; } 2>&1'
  if [ "$NATIVE" -eq 1 ]; then
    out="$(DATABASE_URL="$TEST_DB_URL_NATIVE" in_backend "$cmd")"; code=$?
  else
    out="$(in_backend "cd /app/backend && $cmd" -e "DATABASE_URL=$TEST_DB_URL_CONTAINER" --no-deps)"; code=$?
  fi
  printf '%s\n' "$out" | sed 's/^/    /'
  [ "$code" -ne 0 ] && { set_result alembic 1 "exit=$code"; return 1; }
  printf '%s' "$out" | grep -q '(head)' || { set_result alembic 1 'reason=alembic-current-not-at-head'; return 1; }
  set_result alembic 0 'roundtrip=clean'
}

# --------------------------------------------------------------------------
# PROFILE: grep
# --------------------------------------------------------------------------
live_backend_files() {
  local f rel d skip
  while IFS= read -r f; do
    rel="${f#"$REPO_ROOT"/}"
    case "$rel" in *"/__pycache__/"*) continue ;; esac
    skip=0
    for d in "${GREP_EXCLUDE_DIRS[@]}"; do
      case "$rel" in "backend/$d/"*) skip=1; break ;; esac
    done
    [ "$skip" -eq 0 ] && printf '%s\n' "$f"
  done < <(find "$REPO_ROOT/backend" -type f -name '*.py')
}

profile_grep() {
  local file_list total_hits=0 total_patterns=0 rc=0
  file_list="$(mktemp)"; live_backend_files | sort > "$file_list"
  local file_count; file_count="$(wc -l < "$file_list" | tr -d ' ')"
  if [ "$file_count" -eq 0 ]; then
    rm -f "$file_list"; set_result grep 1 'reason=no-live-backend-files-found (wrong repo root?)'; return 1
  fi

  local pf path patterns pattern hits n
  for pf in "${GREP_FILES[@]}"; do
    path="$pf"; is_abs "$path" || path="$REPO_ROOT/$pf"
    if [ ! -f "$path" ]; then say "[grep] FAIL patternfile=$pf reason=not-found"; rc=1; continue; fi
    patterns="$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$path")"
    n="$(printf '%s\n' "$patterns" | grep -c . )"
    total_patterns=$((total_patterns + n))
    say "[grep] START patternfile=$pf patterns=$n files=$file_count"
    if [ "$n" -eq 0 ]; then say "[grep] FAIL patternfile=$pf reason=no-patterns"; rc=1; continue; fi

    # One grep per pattern: GNU grep -P does not accept a multi-pattern -f.
    while IFS= read -r pattern; do
      [ -z "$pattern" ] && continue
      hits="$(xargs -a "$file_list" -d '\n' grep -nHP -- "$pattern" 2>/dev/null)"
      if [ -n "$hits" ]; then
        while IFS= read -r line; do
          say "[grep] HIT ${line#"$REPO_ROOT"/}"
          total_hits=$((total_hits + 1))
        done <<< "$hits"
        rc=1
      fi
    done <<< "$patterns"
  done

  rm -f "$file_list"
  set_result grep "$rc" "hits=$total_hits patterns=$total_patterns files=$file_count"
}

# --------------------------------------------------------------------------
# PROFILE: tests
# --------------------------------------------------------------------------
profile_tests() {
  local paths=() spec matched
  for spec in "${TEST_SPECS[@]}"; do
    if [[ "$spec" == *[\*\?]* ]]; then
      matched="$(cd "$REPO_ROOT/backend" && ls -1 $spec 2>/dev/null)"
      if [ -z "$matched" ]; then say "[tests] FAIL spec=$spec reason=glob-matched-nothing"; set_result tests 1 'reason=path-resolution'; return 1; fi
      while IFS= read -r m; do paths+=("$m"); done <<< "$matched"
    else
      if [ ! -e "$REPO_ROOT/backend/$spec" ]; then say "[tests] FAIL spec=$spec reason=path-not-found"; set_result tests 1 'reason=path-resolution'; return 1; fi
      paths+=("$spec")
    fi
  done
  say "[tests] START paths=$(IFS=,; echo "${paths[*]}")"
  ensure_test_db || { set_result tests 1 'reason=test-db-unavailable'; return 1; }

  local cmd out code
  cmd="{ python -m pytest -q -rs -p no:cacheprovider ${paths[*]}; } 2>&1"
  if [ "$NATIVE" -eq 1 ]; then
    out="$(TEST_DATABASE_URL="$TEST_DB_URL_NATIVE" in_backend "$cmd")"; code=$?
  else
    out="$(in_backend "cd /app/backend && $cmd" -e "TEST_DATABASE_URL=$TEST_DB_URL_CONTAINER" --no-deps)"; code=$?
  fi
  printf '%s\n' "$out" | sed 's/^/    /'

  local passed skipped failed errors
  passed="$(printf  '%s' "$out" | grep -oE '[0-9]+ passed'  | head -1 | grep -oE '[0-9]+')"; passed="${passed:-0}"
  skipped="$(printf '%s' "$out" | grep -oE '[0-9]+ skipped' | head -1 | grep -oE '[0-9]+')"; skipped="${skipped:-0}"
  failed="$(printf  '%s' "$out" | grep -oE '[0-9]+ failed'  | head -1 | grep -oE '[0-9]+')"; failed="${failed:-0}"
  errors="$(printf  '%s' "$out" | grep -oE '[0-9]+ error'   | head -1 | grep -oE '[0-9]+')"; errors="${errors:-0}"
  local info="passed=$passed skipped=$skipped failed=$failed errors=$errors exit=$code"

  # The whole reason this profile exists: a PG suite that SKIPS (no
  # TEST_DATABASE_URL, no Docker, missing fixture) exits 0 and reads as green.
  # Here it is a hard failure.
  if [ "$skipped" -gt 0 ]; then
    say '[tests] SKIP-AS-FAIL a Postgres test skipped; skipped-as-green is a failure by policy'
    set_result tests 1 "$info"; return 1
  fi
  [ "$passed" -eq 0 ] && { set_result tests 1 "$info reason=no-tests-ran"; return 1; }
  [ "$code" -ne 0 ]   && { set_result tests 1 "$info"; return 1; }
  set_result tests 0 "$info"
}

# --------------------------------------------------------------------------
# PROFILE: boot
# --------------------------------------------------------------------------
profile_boot() {
  say '[boot] START up -d --force-recreate backend'
  docker compose -f "$COMPOSE_MAIN" up -d --force-recreate backend >/dev/null 2>&1 \
    || { set_result boot 1 'reason=compose-up-failed'; return 1; }

  local id t0 elapsed healthy=true log started tracebacks
  id="$(docker compose -f "$COMPOSE_MAIN" ps -q backend | head -1)"
  t0=$SECONDS
  wait_state "$id" healthy "$BOOT_TIMEOUT" || healthy=false
  elapsed=$((SECONDS - t0))

  # A force-recreated container's log contains exactly this boot, nothing else.
  log="$(docker compose -f "$COMPOSE_MAIN" logs --no-log-prefix backend 2>/dev/null)"
  started="$(printf '%s' "$log"    | grep -c 'Application startup complete')"
  tracebacks="$(printf '%s' "$log" | grep -c 'Traceback (most recent call last)')"

  if [ "$healthy" != true ] || [ "$started" -eq 0 ] || [ "$tracebacks" -gt 0 ]; then
    say '[boot] LOGTAIL (health-probe lines removed)'
    printf '%s\n' "$log" | grep -v '/api/health' | tail -60 | sed 's/^/    /'
    set_result boot 1 "healthy=$healthy startup_complete=$started tracebacks=$tracebacks elapsed=${elapsed}s"
    return 1
  fi
  set_result boot 0 "healthy=true startup_complete=$started tracebacks=0 elapsed=${elapsed}s"
}

# --------------------------------------------------------------------------
# PROFILE: smoke
# --------------------------------------------------------------------------
dotenv_value() { # key -> value on stdout; NEVER logged
  local key="$1" v
  v="$(printenv "$key" 2>/dev/null)"
  if [ -n "$v" ]; then printf '%s' "$v"; return; fi
  [ -f "$REPO_ROOT/backend/.env" ] || return
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$REPO_ROOT/backend/.env" \
    | head -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

http_code() { # method url [bearer] [json-body-file]
  local method="$1" url="$2" bearer="${3:-}" body="${4:-}"
  local args=(-s -o "$HTTP_BODY" -w '%{http_code}' -X "$method" --max-time 30)
  [ -n "$bearer" ] && args+=(-H "Authorization: Bearer $bearer")
  [ -n "$body" ]   && args+=(-H 'Content-Type: application/json' --data-binary "@$body")
  curl "${args[@]}" "$url" 2>/dev/null || echo 000
}

profile_smoke() {
  say "[smoke] START base=$BASE_URL migrated=$SMOKE_MIGRATED legacy=$SMOKE_LEGACY"
  local email pass
  email="$(dotenv_value ADMIN_EMAIL)"; pass="$(dotenv_value ADMIN_PASSWORD)"
  if [ -z "$email" ] || [ -z "$pass" ]; then
    set_result smoke 1 'reason=ADMIN_EMAIL/ADMIN_PASSWORD-unset (names only; values never read into output)'; return 1
  fi

  HTTP_BODY="$(mktemp)"; LOGIN_BODY="$(mktemp)"
  trap 'rm -f "$HTTP_BODY" "$LOGIN_BODY"; teardown_test_db' EXIT

  local code token me_email mig leg
  code="$(http_code GET "$BASE_URL/api/health")"
  [ "$code" != 200 ] && { set_result smoke 1 "health=$code"; return 1; }

  python3 - "$email" "$pass" > "$LOGIN_BODY" <<'PY' 2>/dev/null || printf '{"email":"%s","password":"%s"}' "$email" "$pass" > "$LOGIN_BODY"
import json, sys
print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))
PY
  code="$(http_code POST "$BASE_URL/api/auth/login" '' "$LOGIN_BODY")"
  [ "$code" != 200 ] && { set_result smoke 1 "health=200 login=$code"; return 1; }
  token="$(sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$HTTP_BODY" | head -1)"
  [ -z "$token" ] && { set_result smoke 1 'health=200 login=200 reason=no-token-in-login-response'; return 1; }

  code="$(http_code GET "$BASE_URL/api/auth/me" "$token")"
  [ "$code" != 200 ] && { set_result smoke 1 "health=200 login=200 me=$code"; return 1; }
  me_email="$(sed -n 's/.*"email"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$HTTP_BODY" | head -1)"
  if [ "$me_email" != "$email" ]; then
    set_result smoke 1 "health=200 login=200 me=200 reason=identity-mismatch expected=$(elide "$email") got=$(elide "$me_email")"; return 1
  fi

  # Parallel-run seam: a Postgres-backed domain and a Mongo-backed domain must
  # both answer 200 in the same request cycle (plan §A10).
  mig="$(http_code GET "$BASE_URL$SMOKE_MIGRATED" "$token")"
  leg="$(http_code GET "$BASE_URL$SMOKE_LEGACY" "$token")"
  local info="health=200 login=200 me=200($(elide "$me_email")) migrated=$SMOKE_MIGRATED:$mig legacy=$SMOKE_LEGACY:$leg"
  { [ "$mig" != 200 ] || [ "$leg" != 200 ]; } && { set_result smoke 1 "$info"; return 1; }
  set_result smoke 0 "$info"
}

# --------------------------------------------------------------------------
# PROFILE: contracts
# --------------------------------------------------------------------------
profile_contracts() {
  say "[contracts] START mode=compare dir=$CAPTURE_DIR"
  local host_dir="$CAPTURE_DIR"
  is_abs "$host_dir" || host_dir="$REPO_ROOT/$CAPTURE_DIR"
  if [ ! -d "$host_dir" ]; then
    set_result contracts 1 "reason=capture-dir-not-found dir=$CAPTURE_DIR (run: capture_contracts.py record)"; return 1
  fi

  local out code
  out="$(in_backend \
    'cd /app/backend && { python /harness/scripts/capture_contracts.py compare --dir /harness/captures --base-url http://backend:8001; } 2>&1' \
    -v "$REPO_ROOT/scripts:/harness/scripts:ro" -v "$host_dir:/harness/captures")"
  code=$?
  printf '%s\n' "$out" | sed 's/^/    /'
  [ "$code" -ne 0 ] && { set_result contracts 1 "exit=$code"; return 1; }
  set_result contracts 0 'shape=unchanged'
}

# --------------------------------------------------------------------------
# Run, in order, stopping at the first failure
# --------------------------------------------------------------------------
FAILED=0
[ "$RUN_ALEMBIC" -eq 1 ]   && [ "$FAILED" -eq 0 ] && { profile_alembic   || FAILED=1; }
[ "$RUN_GREP" -eq 1 ]      && [ "$FAILED" -eq 0 ] && { profile_grep      || FAILED=1; }
[ "$RUN_TESTS" -eq 1 ]     && [ "$FAILED" -eq 0 ] && { profile_tests     || FAILED=1; }
[ "$RUN_BOOT" -eq 1 ]      && [ "$FAILED" -eq 0 ] && { profile_boot      || FAILED=1; }
[ "$RUN_SMOKE" -eq 1 ]     && [ "$FAILED" -eq 0 ] && { profile_smoke     || FAILED=1; }
[ "$RUN_CONTRACTS" -eq 1 ] && [ "$FAILED" -eq 0 ] && { profile_contracts || FAILED=1; }

SUMMARY=""
for p in "${SELECTED[@]}"; do
  if [ -n "${RESULTS[$p]:-}" ]; then SUMMARY="$SUMMARY $p=${RESULTS[$p]}"; fi
done
NOTRUN=""
for p in "${SELECTED[@]}"; do
  if [ -z "${RESULTS[$p]:-}" ]; then NOTRUN="$NOTRUN,$p"; fi
done
[ -n "$NOTRUN" ] && SUMMARY="$SUMMARY not-run=${NOTRUN#,}"

if [ "$FAILED" -ne 0 ]; then say "[verify] RESULT=FAIL$SUMMARY"; exit 1; fi
say "[verify] RESULT=PASS$SUMMARY"
exit 0
