from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from .models import Paragraph, TocEntry


ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
DIACRITIC_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")


def clean_interface_text(text: str) -> str:
    """Remove only edge/interface whitespace; never normalize Arabic content."""

    return text.replace("\xa0", " ").strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ParsedPage:
    page_index: int
    part: int
    page_number: str | None
    paragraphs: list[Paragraph]
    headings: list[str]

    @property
    def raw_text(self) -> str:
        return "\n\n".join(paragraph.raw_text for paragraph in self.paragraphs)

    @property
    def clean_text(self) -> str:
        return "\n\n".join(paragraph.clean_text for paragraph in self.paragraphs)


@dataclass(slots=True)
class VolumePart:
    part: int
    request_part: str
    start_page: int
    page_count: int
    title: str

    @property
    def end_page(self) -> int:
        return self.start_page + self.page_count - 1


@dataclass(slots=True)
class VolumePage:
    page_index: int
    part: int
    request_part: str


@dataclass(slots=True)
class ParsedDocument:
    title: str
    book_title: str
    pages: list[ParsedPage]
    toc_entries: list[TocEntry]
    volume_numbers: list[int]
    volume_parts: list[VolumePart]
    volume_pages: list[VolumePage]
    slider_max: int | None
    slider_value: int | None
    printed_total: int | None
    meta_description: str | None
    breadcrumb_titles: list[str]
    active_index_path: str | None


@dataclass(slots=True)
class EmbeddedPageData:
    page: ParsedPage
    breadcrumb_titles: list[str]
    active_index_path: str | None


@dataclass
class _StackEntry:
    tag: str
    attrs: dict[str, str]
    classes: set[str]
    page_marker: bool = False
    paragraph_marker: bool = False
    copy_marker: bool = False
    heading_marker: bool = False
    toc_marker: bool = False
    title_marker: bool = False
    li_context: dict[str, str | None] | None = None


@dataclass
class _PageBuilder:
    page_index: int
    paragraphs: list[Paragraph] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)


@dataclass
class _ParagraphBuilder:
    paragraph_id: str
    raw_chunks: list[str] = field(default_factory=list)
    copy_attrs: dict[str, str] = field(default_factory=dict)


class KetabHTMLParser(HTMLParser):
    """Purpose-built tolerant parser for the public KetabOnline reader HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_StackEntry] = []
        self.title_chunks: list[str] = []
        self.meta_description: str | None = None
        self.pages: list[ParsedPage] = []
        self.toc_entries: list[TocEntry] = []
        self.volume_numbers: set[int] = set()
        self.slider_max: int | None = None
        self.slider_value: int | None = None
        self.footer_chunks: list[str] = []
        self._page: _PageBuilder | None = None
        self._paragraph: _ParagraphBuilder | None = None
        self._heading_chunks: list[str] | None = None
        self._toc: dict[str, str | None] | None = None
        self._toc_text: list[str] = []
        self._copy_depth = 0
        self._footer_depth = 0

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    @staticmethod
    def _classes(attrs: dict[str, str]) -> set[str]:
        return set(attrs.get("class", "").split())

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = self._attrs(attrs_list)
        classes = self._classes(attrs)
        entry = _StackEntry(tag=tag, attrs=attrs, classes=classes)

        if tag == "title":
            entry.title_marker = True

        if tag == "meta" and attrs.get("name") == "description":
            self.meta_description = attrs.get("content") or None

        if tag == "input" and attrs.get("type") == "range":
            self.slider_max = _safe_int(attrs.get("max"))
            self.slider_value = _safe_int(attrs.get("value"))

        if "bookContent-footer" in classes:
            self._footer_depth += 1
        elif self._footer_depth:
            self._footer_depth += 1

        if tag == "li":
            entry.li_context = {"own_index": None}

        if tag == "span" and attrs.get("aria-controls", "").startswith("index-"):
            context = self._nearest_li_context()
            if context is not None:
                context["own_index"] = attrs["aria-controls"].removeprefix("index-")

        page_index = _safe_int(attrs.get("book-page-index"))
        if page_index is not None and "book-page" in classes:
            if self._page is not None:
                self._finalize_page()
            self._page = _PageBuilder(page_index=page_index)
            entry.page_marker = True

        if tag == "p" and "g-paragraph" in classes and self._page is not None:
            if self._paragraph is not None:
                self._finalize_paragraph()
            self._paragraph = _ParagraphBuilder(paragraph_id=attrs.get("id", ""))
            entry.paragraph_marker = True

        if self._paragraph is not None and tag == "a" and "g-copy" in classes:
            self._paragraph.copy_attrs = dict(attrs)
            self._copy_depth += 1
            entry.copy_marker = True
        elif self._copy_depth:
            self._copy_depth += 1

        if self._paragraph is not None and tag == "span" and "g-square-brackets" in classes:
            self._heading_chunks = []
            entry.heading_marker = True

        if tag == "a" and "BookSideMenuItem" in classes:
            href = attrs.get("href", "")
            query = parse_qs(urlparse(href).query)
            part = _part_number(_first(query.get("part")))
            page = _safe_int(_first(query.get("page")))
            if part is not None and page is not None:
                context = self._nearest_li_context()
                parent = self._nearest_index_id()
                self._toc = {
                    "part": str(part),
                    "page": str(page),
                    "href": href,
                    "title": attrs.get("rel", ""),
                    "index_id": context.get("own_index") if context else None,
                    "parent_index_id": parent,
                }
                self._toc_text = []
                entry.toc_marker = True

        self.stack.append(entry)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if any(entry.title_marker for entry in self.stack):
            self.title_chunks.append(data)
        if self._footer_depth:
            self.footer_chunks.append(data)
        if self._toc is not None:
            self._toc_text.append(data)
        if self._paragraph is not None and self._copy_depth == 0:
            self._paragraph.raw_chunks.append(data)
        if self._heading_chunks is not None:
            self._heading_chunks.append(data)
        for match in re.finditer(r"المجلد\s*\((\d+)\)", data):
            number = int(match.group(1))
            if number > 0:
                self.volume_numbers.add(number)

    def handle_endtag(self, tag: str) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i].tag == tag), None)
        if index is None:
            return
        popped = self.stack[index:]
        del self.stack[index:]

        for entry in reversed(popped):
            if entry.copy_marker:
                self._copy_depth = 0
            elif self._copy_depth:
                self._copy_depth = max(0, self._copy_depth - 1)

            if entry.heading_marker:
                heading = clean_interface_text("".join(self._heading_chunks or []))
                if heading and self._page is not None and heading not in self._page.headings:
                    self._page.headings.append(heading)
                self._heading_chunks = None

            if entry.paragraph_marker:
                self._finalize_paragraph()

            if entry.toc_marker:
                self._finalize_toc()

            if entry.page_marker:
                self._finalize_page()

        if self._footer_depth:
            self._footer_depth = max(0, self._footer_depth - len(popped))

    def close(self) -> None:
        super().close()
        if self._paragraph is not None:
            self._finalize_paragraph()
        if self._page is not None:
            self._finalize_page()
        if self._toc is not None:
            self._finalize_toc()

    def _nearest_li_context(self) -> dict[str, str | None] | None:
        for entry in reversed(self.stack):
            if entry.li_context is not None:
                return entry.li_context
        return None

    def _nearest_index_id(self) -> str | None:
        for entry in reversed(self.stack):
            element_id = entry.attrs.get("id", "")
            if element_id.startswith("index-"):
                return element_id.removeprefix("index-")
        return None

    def _finalize_toc(self) -> None:
        if self._toc is None:
            return
        title = clean_interface_text(self._toc.get("title") or "")
        if not title:
            visible = re.sub(r"\s+", " ", "".join(self._toc_text)).strip()
            title = re.sub(r"\s*مجلد\s+\d+-\d+\s*$", "", visible).strip()
        self.toc_entries.append(
            TocEntry(
                part=int(self._toc["part"] or 0),
                page_index=int(self._toc["page"] or 0),
                title=title,
                href=self._toc["href"] or "",
                index_id=self._toc.get("index_id"),
                parent_index_id=self._toc.get("parent_index_id"),
            )
        )
        self._toc = None
        self._toc_text = []

    def _finalize_paragraph(self) -> None:
        if self._paragraph is None or self._page is None:
            self._paragraph = None
            return
        raw_text = "".join(self._paragraph.raw_chunks)
        attrs = self._paragraph.copy_attrs
        paragraph_id = attrs.get("data-id") or self._paragraph.paragraph_id
        paragraph = Paragraph(
            paragraph_id=paragraph_id,
            raw_text=raw_text,
            clean_text=clean_interface_text(raw_text),
            book_id=_safe_int(attrs.get("data-book-id")),
            page_id=_safe_int(attrs.get("data-page-id")),
            printed_page=attrs.get("data-number") or None,
            part=_part_number(attrs.get("data-part-name")),
            local_index=_safe_int(attrs.get("data-index")),
            next_data_id=attrs.get("next-data-id") or None,
        )
        if paragraph.raw_text or paragraph.paragraph_id:
            self._page.paragraphs.append(paragraph)
        self._paragraph = None

    def _finalize_page(self) -> None:
        if self._page is None:
            return
        first = self._page.paragraphs[0] if self._page.paragraphs else None
        part = first.part if first and first.part is not None else 0
        page_number = first.printed_page if first else None
        self.pages.append(
            ParsedPage(
                page_index=self._page.page_index,
                part=part,
                page_number=page_number,
                paragraphs=self._page.paragraphs,
                headings=self._page.headings,
            )
        )
        self._page = None


def parse_document(html: str) -> ParsedDocument:
    parser = KetabHTMLParser()
    parser.feed(html)
    parser.close()
    title = clean_interface_text("".join(parser.title_chunks))
    book_title = title.split("|")[0].strip() if title else ""
    footer = re.sub(r"\s+", " ", "".join(parser.footer_chunks)).strip()
    total_match = re.search(r"/\s*(\d+)", footer)
    printed_total = int(total_match.group(1)) if total_match else None
    pages = sorted(parser.pages, key=lambda page: page.page_index)
    breadcrumb_titles: list[str] = []
    active_index_path: str | None = None
    if not pages:
        embedded = parse_embedded_page(html)
        if embedded is not None:
            pages = [embedded.page]
            breadcrumb_titles = embedded.breadcrumb_titles
            active_index_path = embedded.active_index_path
    return ParsedDocument(
        title=title,
        book_title=book_title,
        pages=pages,
        toc_entries=parser.toc_entries,
        volume_numbers=sorted(parser.volume_numbers),
        volume_parts=parse_embedded_volume_parts(html),
        volume_pages=parse_embedded_volume_pages(html),
        slider_max=parser.slider_max,
        slider_value=parser.slider_value,
        printed_total=printed_total,
        meta_description=parser.meta_description,
        breadcrumb_titles=breadcrumb_titles,
        active_index_path=active_index_path,
    )


def parse_embedded_page(html: str) -> EmbeddedPageData | None:
    """Extract Nuxt's server-provided pageInView without executing page JavaScript."""

    payload = _parse_nuxt_payload(html)
    if payload is None:
        return None
    body, values = payload

    marker = "pageInView:{"
    start = body.find(marker)
    if start < 0:
        return None
    page_object = body[start + len(marker) :]
    content_match = re.search(r"content:(\"(?:\\.|[^\"\\])*\")", page_object)
    id_match = re.match(r"id:([^,]+),page:([^,]+),", page_object)
    if not content_match or not id_match:
        return None
    page_index_value = _resolve_javascript_token(id_match.group(1).strip(), values)
    if not isinstance(page_index_value, int):
        return None
    try:
        content = json.loads(content_match.group(1))
    except json.JSONDecodeError:
        return None
    wrapped = (
        f'<div book-page-index="{page_index_value}" class="book-page">'
        f'<div class="BookContentInner">{content}</div></div>'
    )
    parser = KetabHTMLParser()
    parser.feed(wrapped)
    parser.close()
    if not parser.pages:
        return None

    breadcrumb_titles: list[str] = []
    breadcrumb_ids: list[str] = []
    breadcrumb_match = re.search(r"breadcrumb:\[([\s\S]*?)\],index:([^,}]+)", page_object)
    if breadcrumb_match:
        for item in re.finditer(r"\{id:([^,]+),page_id:([^,]+),title:([^}]+)\}", breadcrumb_match.group(1)):
            identifier = _resolve_javascript_token(item.group(1).strip(), values)
            title = _resolve_javascript_token(item.group(3).strip(), values)
            if isinstance(identifier, int):
                breadcrumb_ids.append(str(identifier))
            if isinstance(title, str) and title:
                breadcrumb_titles.append(title)
        active = _resolve_javascript_token(breadcrumb_match.group(2).strip(), values)
        if isinstance(active, int) and str(active) not in breadcrumb_ids:
            breadcrumb_ids.append(str(active))
    return EmbeddedPageData(
        page=parser.pages[0],
        breadcrumb_titles=breadcrumb_titles,
        active_index_path="/".join(breadcrumb_ids) or None,
    )


def parse_embedded_volume_parts(html: str) -> list[VolumePart]:
    payload = _parse_nuxt_payload(html)
    if payload is None:
        return []
    body, values = payload
    marker = "parts:["
    marker_start = body.find(marker)
    if marker_start < 0:
        return []
    array_start = marker_start + len("parts:")
    array_text = _extract_javascript_bracketed(body, array_start, "[", "]")
    if array_text is None:
        return []

    result: list[VolumePart] = []
    seen: set[tuple[int, int]] = set()
    for raw_item in _split_javascript_arguments(array_text[1:-1]):
        item = raw_item.strip()
        if not (item.startswith("{") and item.endswith("}")):
            continue
        fields: dict[str, object] = {}
        for raw_field in _split_javascript_arguments(item[1:-1]):
            key, separator, token = raw_field.partition(":")
            if separator and key in {"name", "page_id", "pages", "title"}:
                fields[key] = _resolve_javascript_token(token.strip(), values)

        request_value = fields.get("name")
        start_page = fields.get("page_id")
        page_count = fields.get("pages")
        title = fields.get("title")
        request_part = str(request_value) if isinstance(request_value, (str, int)) else ""
        part = _part_number(request_part)
        if (
            part is None
            or not isinstance(start_page, int)
            or not isinstance(page_count, int)
            or start_page < 0
            or page_count < 1
        ):
            continue
        key = (part, start_page)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            VolumePart(
                part=part,
                request_part=request_part,
                start_page=start_page,
                page_count=page_count,
                title=title if isinstance(title, str) else "",
            )
        )
    return sorted(result, key=lambda item: item.start_page)


def parse_embedded_volume_pages(html: str) -> list[VolumePage]:
    payload = _parse_nuxt_payload(html)
    if payload is None:
        return []
    body, values = payload
    marker = "pages_list:["
    marker_start = body.find(marker)
    if marker_start < 0:
        return []
    array_start = marker_start + len("pages_list:")
    array_text = _extract_javascript_bracketed(body, array_start, "[", "]")
    if array_text is None:
        return []

    result: list[VolumePage] = []
    seen: set[tuple[int, int]] = set()
    for raw_item in _split_javascript_arguments(array_text[1:-1]):
        item = raw_item.strip()
        if not (item.startswith("{") and item.endswith("}")):
            continue
        fields: dict[str, object] = {}
        for raw_field in _split_javascript_arguments(item[1:-1]):
            key, separator, token = raw_field.partition(":")
            if separator and key in {"id", "part"}:
                fields[key] = _resolve_javascript_token(token.strip(), values)

        page_index = fields.get("id")
        request_value = fields.get("part")
        request_part = str(request_value) if isinstance(request_value, (str, int)) else ""
        part = _part_number(request_part)
        if not isinstance(page_index, int) or page_index < 0 or part is None:
            continue
        key = (part, page_index)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            VolumePage(
                page_index=page_index,
                part=part,
                request_part=request_part,
            )
        )
    return sorted(result, key=lambda item: item.page_index)


def _parse_nuxt_payload(html: str) -> tuple[str, dict[str, object]] | None:
    prefix = "window.__NUXT__=(function("
    assignment_start = html.find(prefix)
    if assignment_start < 0:
        return None
    parameters_start = assignment_start + len(prefix)
    body_start_marker = html.find("){", parameters_start)
    script_end = html.find("</script>", body_start_marker)
    if body_start_marker < 0 or script_end < 0:
        return None
    script = html[assignment_start:script_end]
    invocation_start = script.rfind("}}(")
    invocation_end = script.rfind("));")
    if invocation_start < 0 or invocation_end < invocation_start:
        return None
    relative_body_start = body_start_marker + 2 - assignment_start
    parameters_text = html[parameters_start:body_start_marker]
    parameters = [item.strip() for item in parameters_text.split(",") if item.strip()]
    body = script[relative_body_start:invocation_start]
    arguments = _split_javascript_arguments(script[invocation_start + 3 : invocation_end])
    values = {name: _parse_javascript_primitive(value) for name, value in zip(parameters, arguments)}
    return body, values


def _extract_javascript_bracketed(
    text: str, start: int, opening: str, closing: str
) -> str | None:
    if start >= len(text) or text[start] != opening:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _split_javascript_arguments(text: str) -> list[str]:
    values: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    for index, character in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character in depths:
            depths[character] += 1
        elif character in closing:
            opener = closing[character]
            depths[opener] = max(0, depths[opener] - 1)
        elif character == "," and not any(depths.values()):
            values.append(text[start:index].strip())
            start = index + 1
    values.append(text[start:].strip())
    return values


def _parse_javascript_primitive(token: str) -> object:
    token = token.strip()
    if token in {"null", "void 0", "undefined"}:
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return float(token)
    if token.startswith('"'):
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token
    return token


def _resolve_javascript_token(token: str, values: dict[str, object]) -> object:
    return values.get(token, _parse_javascript_primitive(token))


def title_sections(document_title: str, book_title: str) -> list[str]:
    parts = [part.strip() for part in document_title.split("|") if part.strip()]
    ignored = {book_title, "الفتاوى", "جامع الكتب الإسلامية"}
    return [
        part
        for part in parts
        if part not in ignored
        and not re.fullmatch(r"مجلد\s+(?:\d+|مقدمة)", part)
        and not re.fullmatch(r"صفحة\s+\d+", part)
    ]


def arabic_character_count(text: str) -> int:
    return len(ARABIC_RE.findall(text))


def diacritic_count(text: str) -> int:
    return len(DIACRITIC_RE.findall(text))


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _part_number(value: str | None) -> int | None:
    numeric = _safe_int(value)
    if numeric is not None:
        return numeric
    normalized = clean_interface_text(value or "")
    if normalized in {"مقدمة", "المقدمة"}:
        return 0
    return None


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None
