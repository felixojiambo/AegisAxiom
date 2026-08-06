#!/usr/bin/env python3
"""API contract capture / compare — the byte-compatibility check for a cutover.

Runs IN-CONTAINER (CLAUDE.md §4: all Python runs in-container). The harness
mounts this file at /harness/scripts and the capture directory at
/harness/captures, then invokes it on the compose network where the backend
answers at http://backend:8001.

    record   log in, hit the endpoint list, write sanitized JSON to <dir>/<name>.json
    compare  re-hit the same list and structurally diff against what is stored

WHY SHAPE, NOT BYTES
    Plan §A2/§A3 move a domain's storage, not its API. A cutover is correct when
    the response *shape* is unchanged: no key added, no key removed, no key
    retyped. Values (ids, timestamps, counts) are volatile by construction, so
    value comparison is OFF by default (`--ignore-values`, the default) and
    opt-in via `--strict-values`.

WHAT IS WRITTEN
    Captures are checked into the repo, so they must carry no secrets. Two
    layers guarantee that:
      1. any key whose name matches SECRET_KEY_RE has its value replaced with
         "<redacted:TYPE>" — the KEY is kept (that is the whole point), only the
         value goes. Covers token/password/secret/api_key/... and e-mail (PII).
      2. `--verify-clean` (run automatically by `record`) re-reads every written
         file and fails if a stored value still looks like a credential.

    Credentials for the login step come from ADMIN_EMAIL / ADMIN_PASSWORD in the
    process environment — inherited from backend/.env by `docker compose run`,
    never passed on a command line, never printed.

EXIT CODES
    0  everything matched (compare) / everything written and verified (record)
    1  a structural difference, a status mismatch, or a dirty capture
    2  operational failure (backend unreachable, login rejected, bad arguments)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is pinned in requirements.txt
    print("[contract] ERROR httpx not importable; run this in-container", file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------
# The endpoint list. Seeded per the harness spec: login response, /api/auth/me,
# tenants list, roles list, contracts list. Override wholesale with
# `--endpoints <file.json>` (same schema) when a phase needs its own set.
#
#   name    capture filename stem
#   method  GET/POST/...
#   path    appended to --base-url
#   auth    send the bearer token from the login step
#   body    JSON body; the literal "$ADMIN_EMAIL"/"$ADMIN_PASSWORD" are
#           substituted from the environment at request time and never stored
#   domain  informational: which datastore backs it today (parallel-run seam)
# --------------------------------------------------------------------------
DEFAULT_ENDPOINTS: List[Dict[str, Any]] = [
    {
        "name": "auth_login",
        "method": "POST",
        "path": "/api/auth/login",
        "auth": False,
        "body": {"email": "$ADMIN_EMAIL", "password": "$ADMIN_PASSWORD"},
        "domain": "postgres (2a)",
    },
    {"name": "auth_me", "method": "GET", "path": "/api/auth/me", "auth": True, "domain": "postgres (2a)"},
    {"name": "tenants_list", "method": "GET", "path": "/api/tenants", "auth": True, "domain": "postgres (2a)"},
    {"name": "roles_list", "method": "GET", "path": "/api/roles", "auth": True, "domain": "mongo (2b)"},
    {"name": "contracts_list", "method": "GET", "path": "/api/contracts", "auth": True, "domain": "mongo (4a)"},
]

SECRET_KEY_RE = re.compile(
    r"(token|password|passwd|secret|api[_-]?key|client[_-]?secret|private[_-]?key"
    r"|credential|authorization|fernet|salt|hash|email)",
    re.IGNORECASE,
)

# A stored value that still looks like a credential fails --verify-clean.
DIRTY_VALUE_RE = re.compile(
    r"(eyJ[A-Za-z0-9_-]{10,}"          # JWT
    r"|\$2[aby]\$\d{2}\$"              # bcrypt
    r"|[A-Za-z0-9+/]{40,}={0,2}$"      # long base64 blob
    r"|\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b" # e-mail
    r")"
)

REDACTED_PREFIX = "<redacted:"
SAMPLE_LIST_LIMIT = 2


# --------------------------------------------------------------------------
# Shape derivation
# --------------------------------------------------------------------------
def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def merge_shape(a: Any, b: Any) -> Any:
    """Union two shapes. `null` is compatible with anything (a nullable field
    must not read as a retype just because this run happened to populate it)."""
    if a == b:
        return a
    if a == "null":
        return b
    if b == "null":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_shape(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        if not a:
            return b
        if not b:
            return a
        return [merge_shape(a[0], b[0])]
    return "|".join(sorted({str(a), str(b)}))


def shape_of(value: Any) -> Any:
    """Type skeleton: dicts keep keys, lists collapse to a single unioned
    element shape, scalars become their type name."""
    if isinstance(value, dict):
        return {k: shape_of(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        merged = shape_of(value[0])
        for item in value[1:]:
            merged = merge_shape(merged, shape_of(item))
        return [merged]
    return type_name(value)


def sanitize(value: Any, key: Optional[str] = None) -> Any:
    """Drop secret/PII VALUES, keep every KEY. Lists are truncated for size."""
    if key is not None and SECRET_KEY_RE.search(key) and not isinstance(value, (dict, list)):
        return REDACTED_PREFIX + type_name(value) + ">"
    if isinstance(value, dict):
        return {k: sanitize(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value[:SAMPLE_LIST_LIMIT]]
    return value


# --------------------------------------------------------------------------
# Structural diff
# --------------------------------------------------------------------------
def diff_shape(expected: Any, actual: Any, path: str = "") -> List[str]:
    """Return human-readable structural differences. `+` added, `-` removed,
    `~` retyped. A `null` on either side never counts as a retype."""
    here = path or "<root>"
    out: List[str] = []

    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) - set(actual)):
            out.append("- {}.{} (was {})".format(here, k, json_brief(expected[k])))
        for k in sorted(set(actual) - set(expected)):
            out.append("+ {}.{} ({})".format(here, k, json_brief(actual[k])))
        for k in sorted(set(actual) & set(expected)):
            out.extend(diff_shape(expected[k], actual[k], "{}.{}".format(here, k) if path else k))
        return out

    if isinstance(expected, list) and isinstance(actual, list):
        if expected and actual:
            out.extend(diff_shape(expected[0], actual[0], here + "[]"))
        # An empty list on either side carries no shape information; the
        # endpoint simply had no rows this run. Not a contract change.
        return out

    if expected != actual:
        if "null" in (expected, actual) and not (isinstance(expected, (dict, list)) or isinstance(actual, (dict, list))):
            return out
        out.append("~ {} ({} -> {})".format(here, json_brief(expected), json_brief(actual)))
    return out


def diff_values(expected: Any, actual: Any, path: str = "") -> List[str]:
    here = path or "<root>"
    out: List[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(actual) & set(expected)):
            out.extend(diff_values(expected[k], actual[k], "{}.{}".format(here, k) if path else k))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        for i in range(min(len(expected), len(actual))):
            out.extend(diff_values(expected[i], actual[i], "{}[{}]".format(here, i)))
        return out
    if expected != actual:
        out.append("v {} ({!r} -> {!r})".format(here, expected, actual))
    return out


def json_brief(value: Any) -> str:
    if isinstance(value, dict):
        return "object[{}]".format(len(value))
    if isinstance(value, list):
        return "array"
    return str(value)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def substitute(body: Any) -> Any:
    if isinstance(body, dict):
        return {k: substitute(v) for k, v in body.items()}
    if isinstance(body, list):
        return [substitute(v) for v in body]
    if isinstance(body, str) and body.startswith("$"):
        value = os.environ.get(body[1:])
        if value is None:
            raise SystemExit("[contract] ERROR {} is unset in the environment".format(body[1:]))
        return value
    return body


def login(client: httpx.Client, base_url: str) -> Tuple[str, Dict[str, Any], int]:
    spec = next(e for e in DEFAULT_ENDPOINTS if e["name"] == "auth_login")
    resp = client.post(base_url + spec["path"], json=substitute(spec["body"]))
    if resp.status_code != 200:
        raise SystemExit(
            "[contract] ERROR login returned {} (ADMIN_EMAIL/ADMIN_PASSWORD from env; "
            "values not shown)".format(resp.status_code)
        )
    body = resp.json()
    token = body.get("token")
    if not token:
        raise SystemExit("[contract] ERROR login response carried no token")
    return token, body, resp.status_code


def fetch(client: httpx.Client, base_url: str, spec: Dict[str, Any], token: str) -> Tuple[int, Any]:
    headers = {"Authorization": "Bearer " + token} if spec.get("auth") else {}
    body = substitute(spec["body"]) if spec.get("body") else None
    resp = client.request(
        spec.get("method", "GET"), base_url + spec["path"], headers=headers, json=body
    )
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"__non_json_body__": resp.text[:200]}


def capture_one(spec: Dict[str, Any], status: int, payload: Any) -> Dict[str, Any]:
    return {
        "name": spec["name"],
        "request": {
            "method": spec.get("method", "GET"),
            "path": spec["path"],
            "auth": bool(spec.get("auth")),
        },
        "domain": spec.get("domain", "unknown"),
        "status": status,
        "shape": shape_of(payload),
        "sample": sanitize(payload),
    }


# --------------------------------------------------------------------------
# Secret verification
# --------------------------------------------------------------------------
def scan_dirty(value: Any, path: str = "") -> List[str]:
    out: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(scan_dirty(v, "{}.{}".format(path, k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(scan_dirty(v, "{}[{}]".format(path, i)))
    elif isinstance(value, str) and not value.startswith(REDACTED_PREFIX):
        if DIRTY_VALUE_RE.search(value):
            out.append(path.lstrip("."))
    return out


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def load_endpoints(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        return DEFAULT_ENDPOINTS
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_record(args: argparse.Namespace) -> int:
    endpoints = load_endpoints(args.endpoints)
    os.makedirs(args.dir, exist_ok=True)
    written, dirty = [], []

    with httpx.Client(timeout=30.0) as client:
        token, login_body, login_status = login(client, args.base_url)
        for spec in endpoints:
            if spec["name"] == "auth_login":
                status, payload = login_status, login_body
            else:
                status, payload = fetch(client, args.base_url, spec, token)
            record = capture_one(spec, status, payload)

            offenders = scan_dirty(record["sample"])
            if offenders:
                dirty.append((spec["name"], offenders))

            out_path = os.path.join(args.dir, spec["name"] + ".json")
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, sort_keys=True)
                fh.write("\n")
            written.append(spec["name"])
            keys = len(record["shape"]) if isinstance(record["shape"], dict) else 1
            print("[contract] RECORD {} status={} keys={} domain={}".format(
                spec["name"], status, keys, record["domain"]))

    if dirty and not args.no_verify_clean:
        for name, offenders in dirty:
            print("[contract] DIRTY {} fields={}".format(name, ",".join(offenders)))
        print("[contract] FAIL captures carry credential-shaped values; not safe to commit")
        return 1

    print("[contract] PASS recorded={} dir={}".format(len(written), args.dir))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    endpoints = load_endpoints(args.endpoints)
    if not os.path.isdir(args.dir):
        print("[contract] FAIL capture dir not found: {}".format(args.dir))
        return 2

    compared, missing, failures = 0, 0, 0
    strict = args.strict_values and not args.ignore_values

    with httpx.Client(timeout=30.0) as client:
        token, login_body, login_status = login(client, args.base_url)
        for spec in endpoints:
            path = os.path.join(args.dir, spec["name"] + ".json")
            if not os.path.exists(path):
                print("[contract] MISSING {} (no stored capture; run `record`)".format(spec["name"]))
                missing += 1
                failures += 1
                continue
            with open(path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)

            if spec["name"] == "auth_login":
                status, payload = login_status, login_body
            else:
                status, payload = fetch(client, args.base_url, spec, token)

            problems: List[str] = []
            if status != stored.get("status"):
                problems.append("! status ({} -> {})".format(stored.get("status"), status))
            problems.extend(diff_shape(stored.get("shape"), shape_of(payload)))
            if strict:
                problems.extend(diff_values(stored.get("sample"), sanitize(payload)))

            compared += 1
            if problems:
                failures += 1
                for p in problems:
                    print("[contract] DIFF {} {}".format(spec["name"], p))
                print("[contract] FAIL {}".format(spec["name"]))
            else:
                print("[contract] OK {} status={} domain={}".format(
                    spec["name"], status, stored.get("domain", "unknown")))

    mode = "shape+values" if strict else "shape-only"
    if failures:
        print("[contract] FAIL compared={} missing={} failed={} mode={}".format(
            compared, missing, failures, mode))
        return 1
    print("[contract] PASS compared={} missing=0 failed=0 mode={}".format(compared, mode))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["record", "compare"])
    ap.add_argument("--dir", default="/harness/captures", help="capture directory")
    ap.add_argument("--base-url", default=os.environ.get("CONTRACT_BASE_URL", "http://backend:8001"))
    ap.add_argument("--endpoints", help="JSON file overriding the built-in endpoint list")
    # Value comparison is OFF by default: ids and timestamps are volatile, so a
    # value-strict default would cry wolf on every run and get switched off.
    ap.add_argument("--ignore-values", action="store_true", default=True,
                    help="(default) compare shape only; ignore value changes")
    ap.add_argument("--strict-values", action="store_true",
                    help="also fail on value changes (use with a frozen dataset)")
    ap.add_argument("--no-verify-clean", action="store_true",
                    help="record: skip the credential-shaped-value scan (not recommended)")
    args = ap.parse_args()

    if args.strict_values:
        args.ignore_values = False

    try:
        return cmd_record(args) if args.mode == "record" else cmd_compare(args)
    except httpx.HTTPError as exc:
        print("[contract] ERROR backend unreachable at {}: {}".format(args.base_url, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
