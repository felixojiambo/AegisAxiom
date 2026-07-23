"""Scheduled TTL cleanup for platform-core auth state (Phase 2a, plan R10).

Postgres has no native TTL index (unlike the Mongo ``expireAfterSeconds``
indexes we retired in Phase 2a). Instead, ``user_sessions`` /
``password_reset_tokens`` / ``oauth_states`` carry expiry columns, reads filter
on them for correctness, and this job periodically sweeps the expired rows so
the tables don't grow unbounded.

Registered on the existing ``AsyncIOScheduler`` in ``server.py`` and therefore
gated by ``DISABLE_BACKGROUND_JOBS`` exactly like its sibling jobs — it does not
run in the local Mongo-only / jobs-disabled dev profile.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def cleanup_expired_auth_state() -> dict:
    """Delete expired sessions, reset tokens, and OAuth states. Returns counts.

    Opens its own Postgres session (scheduler jobs have no request scope) and
    commits. Safe no-op when nothing is expired.
    """
    from db.session import get_sessionmaker
    from repositories.auth import AuthRepository

    sm = get_sessionmaker()
    async with sm() as session:
        repo = AuthRepository(session)
        sessions = await repo.cleanup_expired_sessions()
        reset_tokens = await repo.cleanup_expired_reset_tokens()
        oauth_states = await repo.cleanup_expired_oauth_states()
        await session.commit()

    counts = {
        "sessions": sessions,
        "reset_tokens": reset_tokens,
        "oauth_states": oauth_states,
    }
    if any(counts.values()):
        logger.info(
            "Auth TTL cleanup: removed %d expired session(s), %d reset token(s), "
            "%d oauth state(s).",
            sessions, reset_tokens, oauth_states,
        )
    return counts
