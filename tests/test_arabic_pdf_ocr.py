from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "ocr_arabic_pdf.py"
SPEC = importlib.util.spec_from_file_location("ocr_arabic_pdf", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ocr = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ocr
SPEC.loader.exec_module(ocr)


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command, timeout: int):
        command = list(command)
        self.commands.append(command)
        executable = Path(command[0]).stem.casefold()
        if executable == "pdftoppm":
            output_prefix = Path(command[-1])
            Path(str(output_prefix) + ".png").write_bytes(ocr.PNG_SIGNATURE + b"x" * 128)
            return ocr.CommandResult(0, "", "")
        if executable == "tesseract":
            return ocr.CommandResult(0, "النص\r\n\r\n\r\n  السطر  \r\n١٢\r\n", "")
        raise AssertionError(f"Unexpected command: {command}")


class ArabicPdfOcrTests(unittest.TestCase):
    def test_pages_are_required_by_cli(self) -> None:
        parser = ocr.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--input", "book.pdf", "--output-dir", "output"])

    def test_parse_pages_sorts_deduplicates_and_expands_ranges(self) -> None:
        self.assertEqual(ocr.parse_pages("10,3-5,4,2"), [2, 3, 4, 5, 10])
        with self.assertRaises(Exception):
            ocr.parse_pages("0")
        with self.assertRaises(Exception):
            ocr.parse_pages("9-3")

    def test_many_pages_require_an_explicit_safety_flag(self) -> None:
        pages = list(range(1, ocr.MAX_PAGES_WITHOUT_CONFIRMATION + 2))
        with self.assertRaises(SystemExit):
            ocr.enforce_page_limit(pages, False)
        ocr.enforce_page_limit(pages, True)

    def test_safe_normalization_does_not_change_arabic_letters_or_diacritics(self) -> None:
        source = "\ufeffبِسْمِ  اللهِ\r\n\r\n\r\nالنَّصّ\u200b  \r\n"
        self.assertEqual(
            ocr.safe_normalize_ocr_text(source),
            "بِسْمِ  اللهِ\n\n\nالنَّصّ",
        )

    def test_selected_pages_are_cached_and_aggregate_is_stably_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_pdf = root / "book.pdf"
            input_pdf.write_bytes(b"%PDF-1.7 test")
            output_dir = root / "output"
            runner = FakeRunner()
            settings = ocr.Settings(dpi=300, language="ara", psm=3, oem=1)
            tools = ocr.Tools(
                pdftoppm=Path("pdftoppm.exe"),
                tesseract=Path("tesseract.exe"),
                tessdata_dir=root / "tessdata",
                pdftoppm_version="pdftoppm 24.0",
                tesseract_version="tesseract 5.5",
                traineddata_sha256={"ara": "model-checksum"},
            )
            source_sha256 = ocr.file_sha256(input_pdf)

            first = ocr.run_selected_pages(
                input_pdf=input_pdf,
                output_dir=output_dir,
                pages=[10, 2],
                source_sha256=source_sha256,
                settings=settings,
                tools=tools,
                workers=2,
                timeout=30,
                force=False,
                runner=runner,
            )
            self.assertEqual([result.pdf_page for result in first], [2, 10])
            self.assertEqual(len(runner.commands), 4)
            self.assertTrue(all(not result.cached for result in first))
            self.assertEqual(first[0].record["printed_page"], 12)
            self.assertEqual(first[0].record["text"], "النص\n\n\n  السطر\n١٢")
            self.assertEqual(
                first[0].record["provenance"]["traineddata_sha256"],
                {"ara": "model-checksum"},
            )

            second = ocr.run_selected_pages(
                input_pdf=input_pdf,
                output_dir=output_dir,
                pages=[10, 2],
                source_sha256=source_sha256,
                settings=settings,
                tools=tools,
                workers=2,
                timeout=30,
                force=False,
                runner=runner,
            )
            self.assertTrue(all(result.cached for result in second))
            self.assertEqual(len(runner.commands), 4)

            ocr.write_aggregate_outputs(
                input_pdf=input_pdf,
                output_dir=output_dir,
                pages=[10, 2],
                source_sha256=source_sha256,
                settings=settings,
                tools=tools,
                results=second,
            )
            lines = (output_dir / "ocr-results.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            records = [json.loads(line) for line in lines]
            self.assertEqual([record["pdf_page"] for record in records], [2, 10])
            self.assertEqual(
                json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))[
                    "cached_pages"
                ],
                [2, 10],
            )


if __name__ == "__main__":
    unittest.main()
