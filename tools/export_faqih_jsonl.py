from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ARABIC_DIACRITICS_RE = re.compile(
    "[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]"
)
TITLE_WRAPPERS = {"[": "]", "(": ")", "{": "}", "«": "»"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a cleaned paragraph-level JSONL export for AI/RAG use."
    )
    parser.add_argument("input_dir", type=Path, help="Directory with part-XX.jsonl files")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to <input_dir>/faqih",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_dir = args.input_dir
    output_dir = args.output_dir or input_dir / "faqih"
    output_dir.mkdir(parents=True, exist_ok=True)

    part_paths = sorted(input_dir.glob("part-[0-9][0-9].jsonl"))
    if not part_paths:
        raise SystemExit(f"No part-XX.jsonl files found in {input_dir}")

    complete_path = output_dir / "complete.faqih.jsonl"
    total_records = 0
    part_counts: dict[str, int] = {}

    with complete_path.open("w", encoding="utf-8", newline="\n") as complete_stream:
        for part_path in part_paths:
            output_path = output_dir / f"{part_path.stem}.faqih.jsonl"
            count = convert_part(part_path, output_path, complete_stream)
            part_counts[output_path.name] = count
            total_records += count

    manifest = {
        "format": "faqih-jsonl-v2",
        "description": (
            "Clean paragraph-level export for AI/RAG. Text is stored only in the text field; "
            "section titles are normalized for search and deduplicated."
        ),
        "source_directory": str(input_dir),
        "record_count": total_records,
        "part_counts": part_counts,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checksums_sha256": {
            path.name: file_sha256(path)
            for path in sorted(output_dir.glob("*.faqih.jsonl"))
        },
    }
    (output_dir / "manifest.faqih.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {total_records} records to {output_dir}")
    return 0


def convert_part(source_path: Path, output_path: Path, complete_stream) -> int:
    count = 0
    with source_path.open("r", encoding="utf-8") as source, output_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        for line in source:
            if not line.strip():
                continue
            page = json.loads(line)
            for paragraph in page.get("paragraphs", []):
                text = clean_text(paragraph.get("clean_text") or "")
                if not text:
                    continue
                record = faqih_record(page, paragraph, text)
                encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
                output.write(encoded + "\n")
                complete_stream.write(encoded + "\n")
                count += 1
    return count


def faqih_record(page: dict[str, Any], paragraph: dict[str, Any], text: str) -> dict[str, Any]:
    book_title = str(page.get("book_title") or "")
    part = int(page.get("part") or 0)
    page_number = page.get("page_number")
    page_index = int(page.get("page_index") or 0)
    paragraph_index = paragraph.get("local_index")
    paragraph_id = str(paragraph.get("paragraph_id") or "")
    citation_page = page_number if page_number not in (None, "") else page_index
    citation_part = "المقدمة" if part == 0 else f"ج {part}"

    return {
        "id": f"{page.get('fragment_id')}:{paragraph_id}",
        "book_id": page.get("book_id"),
        "book_title": book_title,
        "part": part,
        "page_index": page_index,
        "page_number": page_number,
        "paragraph_index": paragraph_index,
        "paragraph_id": paragraph_id,
        "section_titles": normalize_section_titles(page.get("section_titles") or []),
        "citation": f"{book_title}, {citation_part}, ص {citation_page}",
        "source_url": page.get("source_url"),
        "text": text,
    }


def clean_text(value: str) -> str:
    return " ".join(value.split())


def normalize_section_titles(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        title = normalize_section_title(value)
        key = title.casefold()
        if not title or key in seen:
            continue
        normalized.append(title)
        seen.add(key)

    return [
        title
        for title in normalized
        if not any(
            other != title and other.startswith(title)
            for other in normalized
        )
    ]


def normalize_section_title(value: Any) -> str:
    title = unicodedata.normalize("NFKC", str(value)).replace("ـ", "")
    title = ARABIC_DIACRITICS_RE.sub("", title)
    title = clean_text(title)

    while (
        len(title) >= 2
        and title[0] in TITLE_WRAPPERS
        and title[-1] == TITLE_WRAPPERS[title[0]]
    ):
        title = title[1:-1].strip()

    return title


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
