"""Shared test constants for HTTP-integration tests.

Imported by test_distributors.py, test_hardware_material_requests.py, etc.
to avoid copy-pasted admin credentials across files.

Required environment variables for integration tests:
    TEST_ADMIN_EMAIL     — admin user email (defaults to support@simplimeta.io)
    TEST_ADMIN_PASSWORD  — admin user password (no default; tests should skip if unset)
"""

import os

TEST_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "support@simplimeta.io")
TEST_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")
