"""
Shared rate limiter instance — imported by both server.py and route modules.
Avoids circular import (server.py → routes → server.py).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
