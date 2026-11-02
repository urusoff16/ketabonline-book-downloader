from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_FILE_COUNT = 10
KNOWN_BOOK_PREFIXES = {
    "1909": "kitab-al-asl",
    "2722": "fatawa-hindiya",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create text-forward Markdown knowledge files for a custom GPT."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Paragraph-level complete.faqih.jsonl file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to <input_file parent>/../GPTs-faqih",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=DEFAULT_FILE_COUNT,
        help=f"Number of Markdown files to create (default: {DEFAULT_FILE_COUNT})",
    )
    parser.add_argument(
        "--filename-prefix",
        help=(
            "ASCII prefix for output filenames. By default, known books use a "
            "short title and other books use book-<id>."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.files < 1:
        raise SystemExit("--files must be at least 1")

    input_file = args.input_file
    output_dir = args.output_dir or input_file.parent.parent / "GPTs-faqih"
    page_blocks, book_title, paragraph_count, book_id = read_page_blocks(input_file)
    if len(page_blocks) < args.files:
        raise SystemExit(
            f"Cannot create {args.files} non-empty files from {len(page_blocks)} pages"
        )

    chunks = balanced_chunks(page_blocks, args.files)
    output_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(args.files)))
    filename_prefix = args.filename_prefix or default_filename_prefix(book_id)
    validate_filename_prefix(filename_prefix)

    for index, chunk in enumerate(chunks, start=1):
        output_path = output_dir / (
            f"{filename_prefix}-{index:0{width}d}-of-{args.files:0{width}d}.md"
        )
        content = render_document(book_title, index, args.files, chunk)
        output_path.write_text(content, encoding="utf-8", newline="\n")

    print(
        f"Wrote {len(chunks)} Markdown files with {len(page_blocks)} pages "
        f"and {paragraph_count} paragraphs to {output_dir}"
    )
    return 0


def read_page_blocks(input_file: Path) -> tuple[list[str], str, int, str]:
    page_blocks: list[str] = []
    current_key: tuple[Any, ...] | None = None
    current_records: list[dict[str, Any]] = []
    book_title = ""
    book_id = ""
    paragraph_count = 0

    with input_file.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            text = str(record.get("text") or "").strip()
            if not text:
                continue

            key = (
                record.get("book_id"),
                record.get("part"),
                record.get("page_index"),
            )
            if current_key is not None and key != current_key:
                page_blocks.append(render_page(current_records))
                current_records = []
            if current_key is not None and key < current_key:
                raise ValueError(
                    f"Input is not ordered by part and page at line {line_number}"
                )

            current_key = key
            current_records.append(record)
            book_title = book_title or str(record.get("book_title") or "")
            book_id = book_id or str(record.get("book_id") or "")
            paragraph_count += 1

    if current_records:
        page_blocks.append(render_page(current_records))
    if not page_blocks:
        raise SystemExit(f"No paragraph records found in {input_file}")

    return page_blocks, book_title, paragraph_count, book_id


def default_filename_prefix(book_id: str) -> str:
    return KNOWN_BOOK_PREFIXES.get(book_id, f"book-{book_id or 'unknown'}")


def validate_filename_prefix(prefix: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not prefix or any(character not in allowed for character in prefix):
        raise SystemExit(
            "--filename-prefix may contain only ASCII letters, digits, '-' and '_'"
        )


def render_page(records: list[dict[str, Any]]) -> str:
    first = records[0]
    citation = str(first.get("citation") or "").strip()
    if not citation:
        citation = fallback_citation(first)

    lines = [f"## {citation}"]
    section_titles = [
        str(title).strip()
        for title in first.get("section_titles") or []
        if str(title).strip()
    ]
    if section_titles:
        lines.extend(("", f"**الأقسام:** {' / '.join(section_titles)}"))

    source_url = str(first.get("source_url") or "").strip()
    if source_url:
        lines.extend(("", f"**الرابط:** {source_url}"))

    for record in records:
        text = str(record.get("text") or "").strip()
        if text:
            lines.extend(("", text))

    return "\n".join(lines).rstrip() + "\n\n"


def fallback_citation(record: dict[str, Any]) -> str:
    book_title = str(record.get("book_title") or "")
    part = record.get("part")
    part_label = "المقدمة" if part == 0 else f"ج {part}"
    page_number = record.get("page_number")
    if page_number in (None, ""):
        page_number = record.get("page_index")
    return f"{book_title}, {part_label}, ص {page_number}"


def balanced_chunks(page_blocks: list[str], count: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    cursor = 0
    remaining_size = sum(len(block) for block in page_blocks)

    for chunk_index in range(count):
        files_left = count - chunk_index
        pages_left = len(page_blocks) - cursor
        if files_left == 1:
            chunk = page_blocks[cursor:]
            chunks.append(chunk)
            break

        target_size = remaining_size / files_left
        max_pages = pages_left - (files_left - 1)
        chunk = []
        chunk_size = 0

        while len(chunk) < max_pages:
            block = page_blocks[cursor]
            if chunk and abs(target_size - chunk_size) <= abs(
                target_size - (chunk_size + len(block))
            ):
                break
            chunk.append(block)
            chunk_size += len(block)
            cursor += 1

        chunks.append(chunk)
        remaining_size -= chunk_size

    return chunks


def render_document(
    book_title: str,
    index: int,
    total: int,
    page_blocks: list[str],
) -> str:
    header = (
        f"# {book_title}\n\n"
        f"**القسم الرقمي:** {index} من {total}\n\n"
    )
    return header + "".join(page_blocks).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
