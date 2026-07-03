"""
Pytest configuration for AEGISAXIOM backend tests.
Adds the backend directory to sys.path so all backend modules are importable.
"""
import sys
import os

# Ensure the backend/ directory is on the path
BACKEND_DIR = os.path.dirname(__file__)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
