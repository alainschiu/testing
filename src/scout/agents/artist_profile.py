"""Loader for the artist profile. Reads prompts/artist_profile.md (and any
PDFs under data/artist/ — Phase 2+) and snapshots a normalised profile blob
into the artist_profile singleton row.
"""
from __future__ import annotations

import json
from pathlib import Path

from scout.config import get_settings
from scout.db import connection
from scout.logging import get_logger
from scout.models import utc_now_iso

log = get_logger("scout.artist_profile")


def _build_profile_blob() -> dict:
    s = get_settings()
    profile_md = s.prompts_dir / "artist_profile.md"
    body = profile_md.read_text(encoding="utf-8") if profile_md.exists() else ""

    pdf_files: list[dict] = []
    if s.artist_dir.exists():
        for p in sorted(s.artist_dir.glob("*.pdf")):
            pdf_files.append({"name": p.name, "size_bytes": p.stat().st_size, "path": str(p)})

    return {
        "source_md": str(profile_md),
        "markdown": body,
        "pdf_files": pdf_files,
        "snapshot_at": utc_now_iso(),
    }


def refresh_artist_profile() -> dict:
    """Idempotent: upsert the singleton row (id=1) with a fresh profile snapshot."""
    blob = _build_profile_blob()
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "INSERT INTO artist_profile (id, data_json, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
            (json.dumps(blob, ensure_ascii=False), now),
        )
    log.info("artist_profile_refreshed", pdfs=len(blob["pdf_files"]), md_chars=len(blob["markdown"]))
    return blob


def load_artist_profile() -> dict | None:
    """Return the cached profile blob, refreshing it if missing."""
    with connection() as conn:
        row = conn.execute("SELECT data_json FROM artist_profile WHERE id=1").fetchone()
    if row is None:
        return refresh_artist_profile()
    return json.loads(row["data_json"])


def artist_profile_path() -> Path:
    return get_settings().prompts_dir / "artist_profile.md"
