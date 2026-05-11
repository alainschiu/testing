"""scout import-application CLI: ingest a past application as an exemplar."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.db import connection, run_migrations
from scout.models import utc_now_iso


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_opp() -> int:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities (title, type, url, url_hash, status, discovered_at, updated_at)"
            " VALUES ('Akademie Solitude', 'residency', 'https://x', 'h1', 'lead', ?, ?)",
            (now, now),
        )
        return int(cur.lastrowid)


def test_import_with_opportunity_id_derives_type_and_funder(
    runner: CliRunner, tmp_path: Path
) -> None:
    run_migrations()
    opp_id = _seed_opp()
    md = tmp_path / "past.md"
    md.write_text("I propose a slow listening room…\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "import-application",
            str(md),
            "--opportunity-id",
            str(opp_id),
            "--result",
            "won",
        ],
    )
    assert result.exit_code == 0, result.stdout
    with connection() as conn:
        row = conn.execute(
            "SELECT type, funder, result, opportunity_id FROM past_applications"
        ).fetchone()
    assert row["type"] == "residency"
    assert row["funder"] == "Akademie Solitude"
    assert row["result"] == "won"
    assert row["opportunity_id"] == opp_id


def test_import_standalone_with_explicit_type(runner: CliRunner, tmp_path: Path) -> None:
    run_migrations()
    md = tmp_path / "past.md"
    md.write_text("...", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "import-application",
            str(md),
            "--type",
            "grant",
            "--funder",
            "Pro Helvetia",
            "--result",
            "shortlisted",
        ],
    )
    assert result.exit_code == 0, result.stdout
    with connection() as conn:
        row = conn.execute("SELECT type, funder, result FROM past_applications").fetchone()
    assert row["type"] == "grant"
    assert row["funder"] == "Pro Helvetia"
    assert row["result"] == "shortlisted"


def test_rejects_pdf_in_v1(runner: CliRunner, tmp_path: Path) -> None:
    run_migrations()
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = runner.invoke(
        app, ["import-application", str(pdf), "--type", "grant", "--result", "won"]
    )
    assert result.exit_code != 0


def test_rejects_invalid_result(runner: CliRunner, tmp_path: Path) -> None:
    run_migrations()
    md = tmp_path / "x.md"
    md.write_text("...", encoding="utf-8")
    result = runner.invoke(
        app, ["import-application", str(md), "--type", "grant", "--result", "maybe"]
    )
    assert result.exit_code != 0
