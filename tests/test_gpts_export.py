from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "export_gpts_markdown.py"
SPEC = importlib.util.spec_from_file_location("export_gpts_markdown", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
export_gpts_markdown = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_gpts_markdown)


class GptsMarkdownExportTests(unittest.TestCase):
    def test_writes_balanced_text_forward_files_without_splitting_pages(self) -> None:
        records = []
        for page_index in range(4):
            for paragraph_index in range(2):
                records.append(
                    {
                        "book_id": 2722,
                        "book_title": "الفتاوى الهندية",
                        "part": 1,
                        "page_index": page_index,
                        "page_number": str(page_index + 2),
                        "paragraph_index": paragraph_index,
                        "section_titles": ["كتاب الطهارة"],
                        "citation": (
                            f"الفتاوى الهندية, ج 1, ص {page_index + 2}"
                        ),
                        "source_url": f"https://example.test/page/{page_index}",
                        "text": f"نص الصفحة {page_index} الفقرة {paragraph_index}",
                    }
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root / "complete.faqih.jsonl"
            output_dir = root / "GPTs-faqih"
            input_file.write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False) for record in records
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

            result = export_gpts_markdown.main(
                [
                    str(input_file),
                    "--output-dir",
                    str(output_dir),
                    "--files",
                    "2",
                ]
            )

            self.assertEqual(result, 0)
            paths = sorted(output_dir.glob("*.md"))
            self.assertEqual(len(paths), 2)
            self.assertEqual(
                [path.name for path in paths],
                ["fatawa-hindiya-01-of-02.md", "fatawa-hindiya-02-of-02.md"],
            )
            contents = [path.read_text(encoding="utf-8") for path in paths]
            combined = "\n".join(contents)
            self.assertNotIn('"text":', combined)
            self.assertIn("**الأقسام:** كتاب الطهارة", combined)
            self.assertIn("**الرابط:** https://example.test/page/0", combined)

            for page_index in range(4):
                matching = [
                    content
                    for content in contents
                    if f"نص الصفحة {page_index} الفقرة 0" in content
                ]
                self.assertEqual(len(matching), 1)
                self.assertIn(
                    f"نص الصفحة {page_index} الفقرة 1",
                    matching[0],
                )
                self.assertEqual(
                    combined.count(
                        f"الفتاوى الهندية, ج 1, ص {page_index + 2}"
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
