"""Smoke env readiness check.

Validates that the backend ``.env`` parses, that required runtime
vars are present, and (when ``RUN_REAL_DRIVE_SMOKE=1``) that the
real Google Drive smoke prerequisites are configured.

Safe-by-default:
  - Never prints secret VALUES. Only the KEY name and a present /
    absent flag.
  - Does NOT call Google APIs.
  - Does NOT verify service-account credentials by impersonating —
    only checks file existence and JSON shape metadata.

Usage::

    python backend/scripts/check_smoke_env.py
    RUN_REAL_DRIVE_SMOKE=1 python backend/scripts/check_smoke_env.py

Exit codes:
  0  all required (and, when opted in, real-Drive) vars present
  1  required runtime var missing
  2  real-Drive smoke opted in and a Drive var is missing
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import Tuple


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / "backend" / ".env"

# Required at every boot. The server's import path raises at module
# load when any of these are absent (see auth_helper.py JWT_SECRET
# guard, database.py MONGO_URL/DB_NAME).
RUNTIME_REQUIRED = ("JWT_SECRET", "MONGO_URL", "DB_NAME")

# Required when the operator opts into real-Drive smoke. The
# resolver in services/google_docs_service.resolve_sow_folder_id
# accepts either SIMPLIMETA_SOW_FOLDER_ID (canonical) or
# GOOGLE_DRIVE_FOLDER_ID (legacy alias) so the check below treats
# them as alternatives.
REAL_DRIVE_REQUIRED_EITHER = ("SIMPLIMETA_SOW_FOLDER_ID", "GOOGLE_DRIVE_FOLDER_ID")
REAL_DRIVE_REQUIRED_ALL = ("GOOGLE_SERVICE_ACCOUNT_FILE", "SOW_TEMPLATE_ID")


def _read_env_file(path: pathlib.Path) -> Tuple[dict, list]:
    """Read the .env file with python-dotenv and return (parsed,
    parse_warnings). Never returns secret values to callers — uses
    presence-only flags from this point on."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("ERROR: python-dotenv is not installed in the active venv.", file=sys.stderr)
        sys.exit(1)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)
    parsed = dotenv_values(str(path)) or {}
    return parsed, []


def _present(parsed: dict, key: str) -> bool:
    val = parsed.get(key)
    if val is None:
        return False
    val = val.strip()
    if not val:
        return False
    # Reject obvious placeholder shapes like <fill-me> or YOUR_KEY_HERE.
    if re.match(r"^<[^>]+>$", val):
        return False
    return True


def _check_sa_file(parsed: dict) -> Tuple[bool, list]:
    """Best-effort SA file presence check. Does NOT print contents."""
    notes: list = []
    sa_path_str = (parsed.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    if not sa_path_str:
        return False, ["GOOGLE_SERVICE_ACCOUNT_FILE empty in .env"]
    # Resolve via the same fallback the service uses.
    candidates = []
    if os.path.isabs(sa_path_str):
        candidates.append(pathlib.Path(sa_path_str))
    else:
        for prefix in ("", "backend/", "/app/backend/"):
            candidates.append(REPO_ROOT / prefix / sa_path_str)
            candidates.append(pathlib.Path(prefix) / sa_path_str)
    found = next((c for c in candidates if c.exists()), None)
    if not found:
        return False, ["GOOGLE_SERVICE_ACCOUNT_FILE path does not resolve to an existing file"]
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except Exception:
        return False, [f"service account JSON at {found.name!r} is not parseable JSON"]
    required_fields = ("type", "client_email", "private_key", "token_uri")
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return False, [f"service account JSON missing fields: {', '.join(missing)}"]
    # Redacted client_email shape only.
    ce = data.get("client_email", "")
    m = re.match(r"^([^@]+)@([^.]+)\.iam\.gserviceaccount\.com$", ce)
    if m:
        acct, proj = m.group(1), m.group(2)
        acct_r = (acct[:3] + "***") if len(acct) > 3 else "***"
        proj_r = (proj[:3] + "***") if len(proj) > 3 else "***"
        notes.append(f"service account client_email shape: {acct_r}@{proj_r}.iam.gserviceaccount.com")
    return True, notes


def main() -> int:
    parsed, _ = _read_env_file(ENV_FILE)
    print(f"==> read {ENV_FILE.name}: {len(parsed)} key(s) parsed")

    runtime_status = {k: _present(parsed, k) for k in RUNTIME_REQUIRED}
    print("==> runtime required (must be set for boot):")
    for k, ok in runtime_status.items():
        print(f"    {'OK' if ok else 'MISSING'}  {k}")

    runtime_missing = [k for k, ok in runtime_status.items() if not ok]
    exit_code = 0
    if runtime_missing:
        print(f"FAIL: {len(runtime_missing)} required runtime var(s) missing", file=sys.stderr)
        exit_code = 1

    if os.environ.get("RUN_REAL_DRIVE_SMOKE") == "1":
        print("==> RUN_REAL_DRIVE_SMOKE=1 — checking real-Drive prereqs")
        folder_ok = any(_present(parsed, k) for k in REAL_DRIVE_REQUIRED_EITHER)
        print(f"    {'OK' if folder_ok else 'MISSING'}  SIMPLIMETA_SOW_FOLDER_ID or GOOGLE_DRIVE_FOLDER_ID")
        if not folder_ok:
            exit_code = max(exit_code, 2)
        for k in REAL_DRIVE_REQUIRED_ALL:
            present = _present(parsed, k)
            print(f"    {'OK' if present else 'MISSING'}  {k}")
            if not present:
                exit_code = max(exit_code, 2)
        sa_ok, notes = _check_sa_file(parsed)
        print(f"    {'OK' if sa_ok else 'MISSING'}  service account JSON file readable + valid shape")
        for note in notes:
            print(f"      note: {note}")
        if not sa_ok:
            exit_code = max(exit_code, 2)
    else:
        print("==> real-Drive smoke skipped (set RUN_REAL_DRIVE_SMOKE=1 to enable)")

    if exit_code == 0:
        print("==> smoke env readiness: OK")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
