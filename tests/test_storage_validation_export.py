from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from ketab_downloader.exporter import export_book
from ketab_downloader.models import Discovery, PartRange
from ketab_downloader.parser import parse_document
from ketab_downloader.storage import FragmentStore
from ketab_downloader.validation import validate_fragments
from ketab_downloader.workflow import build_fragment


FIXTURE = Path(__file__).parent / "fixtures" / "reader_sample.html"


def discovery() -> Discovery:
    return Discovery(
        book_id=77,
        book_title="كتاب الاختبار",
        source_url="https://ketabonline.com/ar/books/77/read?part=1&page=0",
        part_count=1,
        parts=[PartRange(part=1, start_page=0, end_page=1, local_slider_max=1, printed_total=3)],
        discovered_at=datetime.now(UTC).isoformat(),
    )


class StorageValidationExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = parse_document(FIXTURE.read_text(encoding="utf-8"))
        self.discovery = discovery()
        self.fragments = [
            build_fragment(page, self.document, self.discovery, retrieved_at="2026-07-17T00:00:00+00:00")
            for page in self.document.pages
        ]

    def test_resume_does_not_duplicate_saved_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with FragmentStore(Path(temp) / "fragments.sqlite3") as store:
                self.assertTrue(store.save_fragment(self.fragments[0]))
                self.assertFalse(store.save_fragment(self.fragments[0]))
                self.assertTrue(store.has_page(0))
                self.assertEqual(store.count(), 1)

    def test_validation_accepts_complete_unicode_sample(self) -> None:
        result = validate_fragments(self.fragments, self.discovery, require_complete=True)
        self.assertTrue(result.ok, result.errors)
        self.assertGreater(result.metrics["diacritics"], 0)

    def test_equal_content_is_warning_not_technical_duplicate(self) -> None:
        self.fragments[1].raw_text = self.fragments[0].raw_text
        self.fragments[1].clean_text = self.fragments[0].clean_text
        self.fragments[1].checksum_sha256 = self.fragments[0].checksum_sha256
        result = validate_fragments(self.fragments, self.discovery, require_complete=True)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.metrics["technical_duplicates"], 0)
        self.assertEqual(result.metrics["equal_content_groups"], 1)

    def test_part_start_uses_volume_title_instead_of_stale_breadcrumbs(self) -> None:
        document = parse_document(FIXTURE.read_text(encoding="utf-8"))
        document.title = "عنوان من المجلد السابق | كتاب الاختبار"
        document.breadcrumb_titles = ["عنوان من المجلد السابق"]
        document.active_index_path = "old/section"
        book_map = discovery()
        book_map.parts[0].title = "المجلد الأول"

        first = build_fragment(
            document.pages[0],
            document,
            book_map,
            retrieved_at="2026-07-17T00:00:00+00:00",
        )
        following = build_fragment(
            document.pages[1],
            document,
            book_map,
            retrieved_at="2026-07-17T00:00:00+00:00",
        )

        self.assertIn("المجلد الأول", first.section_titles)
        self.assertNotIn("عنوان من المجلد السابق", first.section_titles)
        self.assertIsNone(first.toc_index_path)
        self.assertNotIn("index=", first.source_url)
        self.assertIn("عنوان من المجلد السابق", following.section_titles)
        self.assertEqual(following.toc_index_path, "old/section")

    def test_exports_txt_markdown_jsonl_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            export_book(output, self.fragments, self.discovery)
            for name in (
                "complete.txt",
                "complete.md",
                "complete.jsonl",
                "manifest.json",
                "download_report.md",
                "part-01.md",
                "part-01.jsonl",
            ):
                self.assertTrue((output / name).is_file(), name)
            self.assertIn("بِسْمِ اللَّهِ", (output / "complete.txt").read_text(encoding="utf-8"))
            part_records = [
                json.loads(line)
                for line in (output / "part-01.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(part_records), len(self.fragments))
            self.assertTrue(all(record["part"] == 1 for record in part_records))


if __name__ == "__main__":
    unittest.main()
