from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .models import Discovery, Fragment
from .parser import arabic_character_count, diacritic_count, sha256_text


INTERFACE_MARKERS = (
    "تسجيل الدخول",
    "سلة المشتروات",
    "البحث في عناوين الكتب",
    "فيسبوك تويتر واتس",
)


@dataclass(slots=True)
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_fragments(
    fragments: list[Fragment],
    discovery: Discovery,
    *,
    expected_pages: set[int] | None = None,
    require_complete: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    ordered = sorted(fragments, key=lambda fragment: fragment.page_index)
    pages = [fragment.page_index for fragment in ordered]
    page_counts = Counter(pages)
    duplicates = sorted(page for page, count in page_counts.items() if count > 1)
    if duplicates:
        result.errors.append(f"Technical duplicate page indexes: {duplicates}")

    expected = expected_pages
    if require_complete:
        expected = discovery.expected_pages
    missing = sorted((expected or set()) - set(pages))
    if missing:
        result.errors.append(f"Missing expected page indexes: {missing[:50]}" + (" …" if len(missing) > 50 else ""))

    invalid_order = [pair for pair in zip(pages, pages[1:]) if pair[1] <= pair[0]]
    if invalid_order:
        result.errors.append(f"Non-increasing page order: {invalid_order[:10]}")

    checksum_errors: list[int] = []
    empty_pages: list[int] = []
    ui_pages: list[int] = []
    metadata_errors: list[str] = []
    arabic_total = 0
    diacritics_total = 0
    content_groups: dict[str, list[int]] = {}

    for fragment in ordered:
        if not fragment.raw_text or not fragment.clean_text:
            empty_pages.append(fragment.page_index)
        if sha256_text(fragment.raw_text) != fragment.checksum_sha256:
            checksum_errors.append(fragment.page_index)
        arabic_total += arabic_character_count(fragment.raw_text)
        diacritics_total += diacritic_count(fragment.raw_text)
        if any(marker in fragment.clean_text for marker in INTERFACE_MARKERS):
            ui_pages.append(fragment.page_index)
        expected_part = discovery.part_for_page(fragment.page_index).part
        if fragment.part != expected_part:
            metadata_errors.append(
                f"page {fragment.page_index}: stored part {fragment.part}, expected {expected_part}"
            )
        if fragment.paragraphs:
            if fragment.first_paragraph_id != fragment.paragraphs[0].paragraph_id:
                metadata_errors.append(f"page {fragment.page_index}: incorrect first paragraph id")
            if fragment.last_paragraph_id != fragment.paragraphs[-1].paragraph_id:
                metadata_errors.append(f"page {fragment.page_index}: incorrect last paragraph id")
        content_groups.setdefault(fragment.checksum_sha256, []).append(fragment.page_index)

    if empty_pages:
        result.errors.append(f"Empty pages: {empty_pages}")
    if checksum_errors:
        result.errors.append(f"Checksum mismatches: {checksum_errors}")
    if ui_pages:
        result.errors.append(f"Possible interface text in book content: {ui_pages}")
    if metadata_errors:
        result.errors.extend(metadata_errors[:50])
    if arabic_total == 0:
        result.errors.append("No Arabic Unicode characters were found")
    if diacritics_total == 0:
        result.errors.append("No Arabic diacritics were found")

    equal_content = [group for group in content_groups.values() if len(group) > 1]
    if equal_content:
        result.warnings.append(
            "Equal page text was found but not treated as a technical duplicate: "
            + json.dumps(equal_content[:20], ensure_ascii=False)
        )

    result.metrics = {
        "validated_fragments": len(ordered),
        "first_page": pages[0] if pages else None,
        "last_page": pages[-1] if pages else None,
        "arabic_characters": arabic_total,
        "diacritics": diacritics_total,
        "missing_expected": len(missing),
        "technical_duplicates": len(duplicates),
        "equal_content_groups": len(equal_content),
        "parts_seen": sorted({fragment.part for fragment in ordered}),
    }
    return result


def write_validation_report(
    path: Path,
    result: ValidationResult,
    fragments: list[Fragment],
    discovery: Discovery,
    *,
    test_mode: bool,
) -> None:
    ordered = sorted(fragments, key=lambda fragment: fragment.page_index)
    lines = [
        "# Отчёт проверки",
        "",
        f"Статус: **{'успешно' if result.ok else 'обнаружены ошибки'}**.",
        f"Режим: {'контрольная выборка' if test_mode else 'основная база'}.",
        "",
        "## Метрики",
        "",
    ]
    for key, value in result.metrics.items():
        lines.append(f"- `{key}`: `{json.dumps(value, ensure_ascii=False)}`")
    lines.extend(["", "## Переходы", ""])
    for fragment in ordered:
        lines.append(
            f"- `{fragment.previous_fragment_id or '∅'}` → `{fragment.fragment_id}` → "
            f"`{fragment.next_fragment_id or '∅'}`; том {fragment.part}, глобальная страница {fragment.page_index}, "
            f"видимая страница {fragment.page_number or '?'}"
        )
    lines.extend(["", "## Ошибки", ""])
    lines.extend([f"- {item}" for item in result.errors] or ["- Нет."])
    lines.extend(["", "## Предупреждения", ""])
    lines.extend([f"- {item}" for item in result.warnings] or ["- Нет."])
    lines.extend(
        [
            "",
            "## Проверенные свойства",
            "",
            "- начало и конец каждого сохранённого фрагмента не пусты;",
            "- арабский Unicode и огласовки присутствуют;",
            "- порядок глобальных индексов однозначен;",
            "- технические дубли определяются по идентичности страницы и метаданных, а не по совпадению текста;",
            "- границы томов сверяются с картой исследования;",
            "- контрольные суммы пересчитаны из `raw_text`;",
            "- типичные элементы интерфейса не обнаружены в тексте книги.",
            "",
            "## Оценка полной выгрузки",
            "",
            (
                f"Схема пригодна для полной последовательной выгрузки {discovery.fragment_count} физических страниц. "
                "Полная выгрузка в этом запуске не выполнялась."
                if test_mode and result.ok
                else "Оценка зависит от устранения перечисленных ошибок." if not result.ok
                else "Основная база прошла проверку."
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
