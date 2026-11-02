from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .models import Discovery, Fragment


def export_book(
    output_dir: Path,
    fragments: list[Fragment],
    discovery: Discovery,
    *,
    failures: list[dict[str, object]] | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(fragments, key=lambda fragment: fragment.page_index)
    txt_path = output_dir / "complete.txt"
    md_path = output_dir / "complete.md"
    jsonl_path = output_dir / "complete.jsonl"

    txt_path.write_text("\n\n".join(fragment.clean_text for fragment in ordered) + "\n", encoding="utf-8", newline="\n")

    markdown: list[str] = [
        f"# {discovery.book_title}",
        "",
        f"Источник: {discovery.source_url}",
        "",
    ]
    current_part: int | None = None
    for fragment in ordered:
        if fragment.part != current_part:
            current_part = fragment.part
            markdown.extend([f"## Том {current_part}", ""])
        markdown.extend(
            [
                f"### Страница {fragment.page_number or '?'} (глобальный индекс {fragment.page_index})",
                "",
            ]
        )
        for title in fragment.section_titles:
            markdown.extend([f"#### {title}", ""])
        markdown.extend([fragment.clean_text, ""])
    md_path.write_text("\n".join(markdown), encoding="utf-8", newline="\n")

    write_jsonl(jsonl_path, ordered)

    part_paths: list[Path] = []
    for part in discovery.parts:
        part_fragments = [fragment for fragment in ordered if fragment.part == part.part]
        part_jsonl_path = output_dir / f"part-{part.part:02d}.jsonl"
        write_jsonl(part_jsonl_path, part_fragments)
        part_paths.append(part_jsonl_path)
        part_lines = [f"# {discovery.book_title} — том {part.part}", ""]
        for fragment in part_fragments:
            part_lines.extend(
                [
                    f"## Страница {fragment.page_number or '?'} (глобальный индекс {fragment.page_index})",
                    "",
                    fragment.clean_text,
                    "",
                ]
            )
        (output_dir / f"part-{part.part:02d}.md").write_text(
            "\n".join(part_lines), encoding="utf-8", newline="\n"
        )

    exported_paths = [txt_path, md_path, jsonl_path, *part_paths]
    checksums = {path.name: file_sha256(path) for path in exported_paths}
    manifest = {
        "book_id": discovery.book_id,
        "book_title": discovery.book_title,
        "part_count": discovery.part_count,
        "fragment_count": len(ordered),
        "expected_fragment_count": discovery.fragment_count,
        "first_identifier": ordered[0].fragment_id if ordered else None,
        "last_identifier": ordered[-1].fragment_id if ordered else None,
        "missing_fragments": sorted(discovery.expected_pages - {f.page_index for f in ordered}),
        "technical_duplicates": [],
        "errors": failures or [],
        "checksums_sha256": checksums,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "program_version": __version__,
        "source_url": discovery.source_url,
        "parts": [part.to_dict() for part in discovery.parts],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    report = [
        "# Отчёт выгрузки",
        "",
        f"- Книга: {discovery.book_title} (`{discovery.book_id}`)",
        f"- Сохранено фрагментов: {len(ordered)} из {discovery.fragment_count}",
        f"- Томов: {discovery.part_count}",
        f"- Ошибок: {len(failures or [])}",
        f"- Исходный URL: {discovery.source_url}",
        "",
        "## Контрольные суммы",
        "",
    ]
    report.extend(f"- `{name}`: `{checksum}`" for name, checksum in checksums.items())
    (output_dir / "download_report.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    return checksums


def write_jsonl(path: Path, fragments: list[Fragment]) -> None:
    lines = [json.dumps(fragment.to_dict(), ensure_ascii=False, sort_keys=True) for fragment in fragments]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
