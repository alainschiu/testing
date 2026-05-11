from scout.agents.artist_profile import load_artist_profile, refresh_artist_profile
from scout.db import connection, run_migrations


def test_refresh_then_load_roundtrip() -> None:
    run_migrations()
    blob = refresh_artist_profile()
    assert "markdown" in blob
    assert "趙朗天" in blob["markdown"] or "Alain Chiu" in blob["markdown"]
    loaded = load_artist_profile()
    assert loaded["markdown"] == blob["markdown"]


def test_refresh_is_idempotent_on_singleton_row() -> None:
    run_migrations()
    refresh_artist_profile()
    refresh_artist_profile()
    with connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM artist_profile").fetchone()["n"]
    assert n == 1
