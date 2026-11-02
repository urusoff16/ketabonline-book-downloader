from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import Discovery, Fragment


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fragments (
    fragment_id TEXT PRIMARY KEY,
    book_id INTEGER NOT NULL,
    book_title TEXT NOT NULL,
    part INTEGER NOT NULL,
    page_index INTEGER NOT NULL UNIQUE,
    page_number TEXT,
    ordinal INTEGER NOT NULL,
    section_titles_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    toc_index_path TEXT,
    previous_fragment_id TEXT,
    next_fragment_id TEXT,
    retrieved_at TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    first_paragraph_id TEXT,
    last_paragraph_id TEXT,
    paragraphs_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fragments_order ON fragments(page_index);

CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part INTEGER NOT NULL,
    page_index INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    error TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


class FragmentStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "FragmentStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def set_metadata(self, key: str, value: object) -> None:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, serialized),
            )

    def get_metadata(self, key: str, default: object = None) -> object:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def save_discovery(self, discovery: Discovery) -> None:
        self.set_metadata("discovery", discovery.to_dict())

    def has_page(self, page_index: int) -> bool:
        row = self.connection.execute("SELECT 1 FROM fragments WHERE page_index=?", (page_index,)).fetchone()
        return row is not None

    def saved_pages(self) -> set[int]:
        return {int(row[0]) for row in self.connection.execute("SELECT page_index FROM fragments")}

    def save_fragment(self, fragment: Fragment) -> bool:
        payload = fragment.to_dict()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO fragments(
                    fragment_id, book_id, book_title, part, page_index, page_number,
                    ordinal, section_titles_json, raw_text, clean_text, source_url,
                    toc_index_path, previous_fragment_id, next_fragment_id, retrieved_at,
                    checksum_sha256, first_paragraph_id, last_paragraph_id, paragraphs_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fragment.fragment_id,
                    fragment.book_id,
                    fragment.book_title,
                    fragment.part,
                    fragment.page_index,
                    fragment.page_number,
                    fragment.ordinal,
                    json.dumps(fragment.section_titles, ensure_ascii=False),
                    fragment.raw_text,
                    fragment.clean_text,
                    fragment.source_url,
                    fragment.toc_index_path,
                    fragment.previous_fragment_id,
                    fragment.next_fragment_id,
                    fragment.retrieved_at,
                    fragment.checksum_sha256,
                    fragment.first_paragraph_id,
                    fragment.last_paragraph_id,
                    json.dumps(payload["paragraphs"], ensure_ascii=False),
                ),
            )
        return cursor.rowcount == 1

    def record_failure(self, part: int, page_index: int, source_url: str, error: str, recorded_at: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO failures(part, page_index, source_url, error, recorded_at) VALUES(?, ?, ?, ?, ?)",
                (part, page_index, source_url, error, recorded_at),
            )

    def fragments(self) -> list[Fragment]:
        rows = self.connection.execute("SELECT * FROM fragments ORDER BY page_index").fetchall()
        return [row_to_fragment(row) for row in rows]

    def failures(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM failures ORDER BY id")]

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM fragments").fetchone()[0])

    def clear(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM fragments")
            self.connection.execute("DELETE FROM failures")
            self.connection.execute("DELETE FROM metadata")

    def iter_jsonl(self) -> Iterable[str]:
        for fragment in self.fragments():
            yield json.dumps(fragment.to_dict(), ensure_ascii=False, sort_keys=True)


def row_to_fragment(row: sqlite3.Row) -> Fragment:
    from .models import Paragraph

    paragraphs = [Paragraph(**item) for item in json.loads(row["paragraphs_json"])]
    return Fragment(
        fragment_id=row["fragment_id"],
        book_id=row["book_id"],
        book_title=row["book_title"],
        part=row["part"],
        index=row["page_index"],
        page_index=row["page_index"],
        page_number=row["page_number"],
        ordinal=row["ordinal"],
        section_titles=json.loads(row["section_titles_json"]),
        raw_text=row["raw_text"],
        clean_text=row["clean_text"],
        source_url=row["source_url"],
        toc_index_path=row["toc_index_path"],
        previous_fragment_id=row["previous_fragment_id"],
        next_fragment_id=row["next_fragment_id"],
        retrieved_at=row["retrieved_at"],
        checksum_sha256=row["checksum_sha256"],
        first_paragraph_id=row["first_paragraph_id"],
        last_paragraph_id=row["last_paragraph_id"],
        paragraphs=paragraphs,
    )
