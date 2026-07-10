"""Tests for the AI Usage Top-Users display-name fallback chain.

Covers the logic introduced after Ryan flagged "Unknown" rows on the AI
Usage dashboard — snapshotted user_email / user_name on the ai_usage row
plus a live users lookup, falling back to user_id (so the actor is still
traceable) and only collapsing to "System / Unknown" when the row truly
has no identity at all.
"""
from routes.ai_usage import _resolve_display_identity


class TestResolveDisplayIdentity:
    def test_prefers_row_snapshot_over_live(self):
        # Row snapshot survives even if the user record was renamed/deleted.
        name, email, fallback = _resolve_display_identity(
            row_name="Phil Keenan",
            row_email="pkeenan@simplimeta.io",
            live_name="Phil K. (renamed)",
            live_email="phil2@simplimeta.io",
            user_id="user_xyz",
        )
        assert name == "Phil Keenan"
        assert email == "pkeenan@simplimeta.io"
        assert fallback is False

    def test_falls_back_to_live_when_row_blank(self):
        name, email, fallback = _resolve_display_identity(
            row_name=None,
            row_email=None,
            live_name="Aidan Harrelson",
            live_email="aharrelson@simplimeta.io",
            user_id="user_398fca1c8694",
        )
        assert name == "Aidan Harrelson"
        assert email == "aharrelson@simplimeta.io"
        assert fallback is False

    def test_uses_email_as_name_when_no_name_anywhere(self):
        # No name on row, no users lookup hit (deleted user) — surface email
        # AND mark name_is_email_fallback so the frontend can render
        # appropriately rather than treating the email as a real name.
        name, email, fallback = _resolve_display_identity(
            row_name=None,
            row_email="legacy@simplimeta.io",
            live_name=None,
            live_email=None,
            user_id="user_deleted",
        )
        assert name == "legacy@simplimeta.io"
        assert email == "legacy@simplimeta.io"
        assert fallback is True

    def test_falls_back_to_user_id_when_no_email_or_name(self):
        # Old rows logged before user_email/user_name were snapshotted, AND
        # the user record was later deleted. Showing user_id keeps the actor
        # traceable instead of collapsing into a generic "Unknown".
        name, email, fallback = _resolve_display_identity(
            row_name=None,
            row_email=None,
            live_name=None,
            live_email=None,
            user_id="user_011bea60f75b",
        )
        assert name == "user_011bea60f75b"
        assert email == ""
        assert fallback is False

    def test_system_unknown_only_when_no_identity_at_all(self):
        # Background / system call with no user_id at all.
        name, email, fallback = _resolve_display_identity(
            row_name=None,
            row_email=None,
            live_name=None,
            live_email=None,
            user_id=None,
        )
        assert name == "System / Unknown"
        assert email == ""
        assert fallback is False

    def test_does_not_collapse_real_user_to_unknown(self):
        # Regression: prior behaviour returned "Unknown" whenever the live
        # users lookup missed, even when user_id was non-empty.
        name, _email, _fallback = _resolve_display_identity(
            row_name=None,
            row_email=None,
            live_name=None,
            live_email=None,
            user_id="user_abc",
        )
        assert name != "Unknown"
