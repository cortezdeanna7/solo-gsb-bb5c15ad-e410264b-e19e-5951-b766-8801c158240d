"""SQLite persistence layer. Schema is created automatically on startup."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = os.environ.get(
    "DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "data" / "svgpaths.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS paths (
    id           TEXT PRIMARY KEY,
    d            TEXT NOT NULL,
    fill_rule    TEXT NOT NULL DEFAULT 'nonzero',
    metadata     TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'received',
    normalized_d TEXT,
    outer_orientation TEXT,
    bounds       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contours (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path_id       TEXT NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
    contour_index INTEGER NOT NULL,
    parent_index  INTEGER,
    depth         INTEGER NOT NULL,
    role          TEXT NOT NULL,
    orientation   TEXT NOT NULL,
    signed_area   REAL NOT NULL,
    point_count   INTEGER NOT NULL,
    d             TEXT NOT NULL,
    excluded      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS warnings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path_id       TEXT NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
    contour_index INTEGER,
    code          TEXT NOT NULL,
    message       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contours_path ON contours(path_id);
CREATE INDEX IF NOT EXISTS idx_warnings_path ON warnings(path_id);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
