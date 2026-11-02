from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "export_faqih_jsonl.py"
SPEC = importlib.util.spec_from_file_location("export_faqih_jsonl", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
export_faqih_jsonl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_faqih_jsonl)


class FaqihExportTests(unittest.TestCase):
    def test_writes_flat_paragraph_records_without_duplicate_page_text(self) -> None:
        page = {
            "book_id": 2722,
            "book_title": "الفتاوى الهندية",
            "clean_text": "duplicate page text",
            "fragment_id": "2722:1:0",
            "page_index": 0,
            "page_number": "2",
            "paragraphs": [
                {
                    "clean_text": "  نص   فقهي   مضبوط  ",
                    "local_index": 1,
                    "paragraph_id": "p-2722-0-1",
                }
            ],
            "part": 1,
            "raw_text": "duplicate raw text",
            "section_titles": [
                "كتاب الطهارة",
                "[كِتَابُ الطَّهَارَةِ]",
                "الباب الأول في الوضوء وفيه خمسة ف",
                "الباب الأول في الوضوء وفيه خمسة فصول",
                "[الْفَصْلُ الْأَوَّلُ]",
            ],
            "source_url": "https://ketabonline.com/ar/books/2722/read?part=1&page=0",
        }

        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory) / "input"
            output_dir = Path(directory) / "output"
            input_dir.mkdir()
            (input_dir / "part-01.jsonl").write_text(
                json.dumps(page, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            result = export_faqih_jsonl.main([str(input_dir), "--output-dir", str(output_dir)])

            self.assertEqual(result, 0)
            record = json.loads((output_dir / "part-01.faqih.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["text"], "نص فقهي مضبوط")
            self.assertEqual(record["citation"], "الفتاوى الهندية, ج 1, ص 2")
            self.assertEqual(record["page_number"], "2")
            self.assertEqual(record["page_index"], 0)
            self.assertEqual(
                record["section_titles"],
                [
                    "كتاب الطهارة",
                    "الباب الأول في الوضوء وفيه خمسة فصول",
                    "الفصل الأول",
                ],
            )
            self.assertNotIn("raw_text", record)
            self.assertNotIn("clean_text", record)
            self.assertNotIn("paragraphs", record)
            self.assertTrue((output_dir / "complete.faqih.jsonl").exists())
            self.assertTrue((output_dir / "manifest.faqih.json").exists())

    def test_introduction_uses_readable_citation_label(self) -> None:
        page = {
            "book_id": 1909,
            "book_title": "الأصل للشيباني",
            "fragment_id": "1909:0:1",
            "page_index": 1,
            "page_number": "1",
            "paragraphs": [
                {
                    "clean_text": "نص المقدمة",
                    "local_index": 1,
                    "paragraph_id": "p-1909-1-1",
                }
            ],
            "part": 0,
            "section_titles": ["مقدمة التحقيق"],
            "source_url": "https://ketabonline.com/ar/books/1909/read?part=مقدمة&page=1",
        }

        record = export_faqih_jsonl.faqih_record(
            page,
            page["paragraphs"][0],
            "نص المقدمة",
        )

        self.assertEqual(record["citation"], "الأصل للشيباني, المقدمة, ص 1")


if __name__ == "__main__":
    unittest.main()
