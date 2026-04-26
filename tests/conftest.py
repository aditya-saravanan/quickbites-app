"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES_SQL = Path(__file__).parent / "fixtures" / "test_fixtures.sql"


@pytest.fixture
def fixture_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(FIXTURES_SQL.read_text(encoding="utf-8"))
    yield conn
    conn.close()
