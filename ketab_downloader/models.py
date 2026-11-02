from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class Paragraph:
    paragraph_id: str
    raw_text: str
    clean_text: str
    book_id: int | None = None
    page_id: int | None = None
    printed_page: str | None = None
    part: int | None = None
    local_index: int | None = None
    next_data_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Fragment:
    fragment_id: str
    book_id: int
    book_title: str
    part: int
    index: int
    page_index: int
    page_number: str | None
    ordinal: int
    section_titles: list[str]
    raw_text: str
    clean_text: str
    source_url: str
    toc_index_path: str | None
    previous_fragment_id: str | None
    next_fragment_id: str | None
    retrieved_at: str
    checksum_sha256: str
    first_paragraph_id: str | None
    last_paragraph_id: str | None
    paragraphs: list[Paragraph] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["paragraphs"] = [paragraph.to_dict() for paragraph in self.paragraphs]
        return result


@dataclass(slots=True)
class TocEntry:
    part: int
    page_index: int
    title: str
    href: str
    index_id: str | None = None
    parent_index_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PartRange:
    part: int
    start_page: int
    end_page: int
    local_slider_max: int
    printed_total: int | None
    request_part: str | None = None
    title: str | None = None
    page_indexes: list[int] | None = None
    excluded_pages: list[int] = field(default_factory=list)
    toc_entries: list[TocEntry] = field(default_factory=list)

    @property
    def fragment_count(self) -> int:
        return len(self.pages)

    @property
    def pages(self) -> Sequence[int]:
        if self.page_indexes is not None:
            return self.page_indexes
        return range(self.start_page, self.end_page + 1)

    def contains(self, page_index: int) -> bool:
        pages = self.pages
        position = bisect_left(pages, page_index)
        return position < len(pages) and pages[position] == page_index

    def exclude_page(self, page_index: int) -> bool:
        if not self.contains(page_index):
            return False
        if self.page_indexes is None:
            self.page_indexes = list(self.pages)
        self.page_indexes.remove(page_index)
        if page_index not in self.excluded_pages:
            self.excluded_pages.append(page_index)
            self.excluded_pages.sort()
        return True

    @property
    def request_value(self) -> str:
        return self.request_part if self.request_part is not None else str(self.part)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["fragment_count"] = self.fragment_count
        result["toc_entries"] = [entry.to_dict() for entry in self.toc_entries]
        return result


@dataclass(slots=True)
class Discovery:
    book_id: int
    book_title: str
    source_url: str
    part_count: int
    parts: list[PartRange]
    discovered_at: str

    @property
    def first_page(self) -> int:
        return min(part.start_page for part in self.parts)

    @property
    def last_page(self) -> int:
        return max(part.end_page for part in self.parts)

    @property
    def fragment_count(self) -> int:
        return sum(part.fragment_count for part in self.parts)

    @property
    def expected_pages(self) -> set[int]:
        return {
            page
            for part in self.parts
            for page in part.pages
        }

    def part_for_page(self, page_index: int) -> PartRange:
        for part in self.parts:
            if part.contains(page_index):
                return part
        raise ValueError(f"Page {page_index} is outside discovered ranges")

    def previous_page(self, page_index: int) -> int | None:
        part = self.part_for_page(page_index)
        pages = part.pages
        position = bisect_left(pages, page_index)
        if position > 0:
            return pages[position - 1]
        previous = [candidate.pages[-1] for candidate in self.parts if candidate.pages[-1] < page_index]
        return max(previous) if previous else None

    def next_page(self, page_index: int) -> int | None:
        part = self.part_for_page(page_index)
        pages = part.pages
        position = bisect_left(pages, page_index)
        if position + 1 < len(pages):
            return pages[position + 1]
        following = [candidate.pages[0] for candidate in self.parts if candidate.pages[0] > page_index]
        return min(following) if following else None

    def ordinal_for_page(self, page_index: int) -> int:
        ordinal = 0
        for part in sorted(self.parts, key=lambda item: item.start_page):
            if part.contains(page_index):
                return ordinal + bisect_left(part.pages, page_index) + 1
            ordinal += part.fragment_count
        raise ValueError(f"Page {page_index} is outside discovered ranges")

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "book_title": self.book_title,
            "source_url": self.source_url,
            "part_count": self.part_count,
            "first_page": self.first_page,
            "last_page": self.last_page,
            "fragment_count": self.fragment_count,
            "discovered_at": self.discovered_at,
            "parts": [part.to_dict() for part in self.parts],
        }
