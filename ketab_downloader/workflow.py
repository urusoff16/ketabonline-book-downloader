from __future__ import annotations

import json
import logging
import signal
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .client import FetchClient, save_problem_response
from .models import Discovery, Fragment, PartRange, TocEntry
from .parser import ParsedDocument, ParsedPage, parse_document, sha256_text, title_sections
from .storage import FragmentStore


BASE_URL = "https://ketabonline.com"


@dataclass(slots=True)
class PageFetch:
    part_range: PartRange
    requested_page: int
    url: str
    response_text: str
    document: ParsedDocument
    matching: list[ParsedPage]
    retrieved_at: str
    confirmed_empty: bool = False


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def book_id_from_url(url: str) -> int:
    parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        index = parts.index("books")
        return int(parts[index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot determine book id from URL: {url}") from exc


def reader_url(
    book_id: int, part: int | str, page_index: int, index_path: str | None = None
) -> str:
    query: dict[str, str | int] = {"part": part, "page": page_index}
    if index_path:
        query["index"] = index_path
    return f"{BASE_URL}/ar/books/{book_id}/read?{urlencode(query)}"


def normalize_source_url(url: str, book_id: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    part = query.get("part", ["1"])[0]
    page = int(query.get("page", ["0"])[0])
    index_path = query.get("index", [None])[0]
    return reader_url(book_id, part, page, index_path)


def discover_book(client: FetchClient, *, book_id: int, source_url: str, output_dir: Path) -> Discovery:
    logger = logging.getLogger(__name__)
    initial_url = normalize_source_url(source_url, book_id)
    initial_result = client.fetch(initial_url)
    initial = parse_document(initial_result.text)
    if initial.volume_parts:
        return discover_book_from_volume_metadata(
            client,
            book_id=book_id,
            source_url=source_url,
            output_dir=output_dir,
            initial=initial,
        )
    volume_numbers = initial.volume_numbers
    if not volume_numbers:
        raise RuntimeError("No volume selector was found in the reader HTML")
    part_count = max(volume_numbers)
    if volume_numbers != list(range(1, part_count + 1)):
        raise RuntimeError(f"Unexpected volume selector values: {volume_numbers}")

    observed: dict[int, tuple[ParsedDocument, list[TocEntry], int]] = {}
    cumulative_start = 0
    for part in range(1, part_count + 1):
        if part == 1:
            document = initial
        else:
            result = client.fetch(reader_url(book_id, part, 0))
            document = parse_document(result.text)
        entries = [entry for entry in document.toc_entries if entry.part == part]
        if document.slider_max is None:
            raise RuntimeError(f"No page-range maximum was found for part {part}")
        if entries:
            start = min(entry.page_index for entry in entries)
            if start != cumulative_start:
                logger.warning(
                    "Part %s TOC starts at %s while continuous range implies %s; using TOC value",
                    part,
                    start,
                    cumulative_start,
                )
        else:
            start = cumulative_start
            logger.info(
                "Part %s TOC is client-rendered; inferred its first global page as %s",
                part,
                start,
            )
        observed[part] = (document, entries, start)
        cumulative_start = start + document.slider_max + 1
        logger.info(
            "Part %s: first global page %s, local maximum %s, %s TOC entries",
            part,
            start,
            document.slider_max,
            len(entries),
        )

    parts: list[PartRange] = []
    starts = {part: values[2] for part, values in observed.items()}
    for part in range(1, part_count + 1):
        document, entries, start = observed[part]
        calculated_end = start + int(document.slider_max or 0)
        if part < part_count:
            next_start = starts[part + 1]
            if calculated_end != next_start - 1:
                logger.warning(
                    "Part %s slider implies end %s but next part begins at %s; using contiguous boundary",
                    part,
                    calculated_end,
                    next_start,
                )
            end = next_start - 1
        else:
            end = calculated_end
        parts.append(
            PartRange(
                part=part,
                start_page=start,
                end_page=end,
                local_slider_max=int(document.slider_max or 0),
                printed_total=document.printed_total,
                toc_entries=deduplicate_toc(entries),
            )
        )

    discovery = Discovery(
        book_id=book_id,
        book_title=initial.book_title,
        source_url=source_url,
        part_count=part_count,
        parts=parts,
        discovered_at=utc_now(),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "discovery.json").write_text(
        json.dumps(discovery.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return discovery


def discover_book_from_volume_metadata(
    client: FetchClient,
    *,
    book_id: int,
    source_url: str,
    output_dir: Path,
    initial: ParsedDocument,
) -> Discovery:
    logger = logging.getLogger(__name__)
    metadata = sorted(initial.volume_parts, key=lambda item: item.start_page)
    internal_parts = [item.part for item in metadata]
    starts = [item.start_page for item in metadata]
    if len(set(internal_parts)) != len(internal_parts):
        raise RuntimeError(f"Duplicate internal volume identifiers: {internal_parts}")
    if len(set(starts)) != len(starts) or starts != sorted(starts):
        raise RuntimeError(f"Unexpected volume start pages: {starts}")

    parts: list[PartRange] = []
    book_title = initial.book_title
    for index, item in enumerate(metadata):
        matching_initial = [
            page
            for page in initial.pages
            if page.page_index == item.start_page and page.part == item.part
        ]
        if matching_initial:
            document = initial
        else:
            result = client.fetch(reader_url(book_id, item.request_part, item.start_page))
            document = parse_document(result.text)
        matching = [
            page
            for page in document.pages
            if page.page_index == item.start_page and page.part == item.part
        ]
        if not matching:
            raise RuntimeError(
                f"Part {item.request_part!r} did not expose its declared first page "
                f"{item.start_page}"
            )
        if document.book_title:
            book_title = document.book_title

        listed_pages = sorted(
            {
                page.page_index
                for page in document.volume_pages
                if page.part == item.part and page.request_part == item.request_part
            }
        )
        if listed_pages and item.start_page not in listed_pages:
            raise RuntimeError(
                f"Part {item.request_part!r} starts at {item.start_page}, "
                "but that page is absent from the server page list"
            )

        expected_slider_max = item.page_count - 1
        if document.slider_max is not None and document.slider_max != expected_slider_max:
            logger.warning(
                "Part %s metadata has %s pages while slider maximum is %s",
                item.request_part,
                item.page_count,
                document.slider_max,
            )

        end_page = listed_pages[-1] if listed_pages else item.end_page
        if not listed_pages and index + 1 < len(metadata):
            next_start = metadata[index + 1].start_page
            if next_start > end_page + 1:
                end_page = find_trailing_part_page(
                    client,
                    book_id=book_id,
                    request_part=item.request_part,
                    internal_part=item.part,
                    declared_end=end_page,
                    next_start=next_start,
                )

        entries = [entry for entry in document.toc_entries if entry.part == item.part]
        parts.append(
            PartRange(
                part=item.part,
                start_page=item.start_page,
                end_page=end_page,
                local_slider_max=(
                    document.slider_max
                    if document.slider_max is not None
                    else expected_slider_max
                ),
                printed_total=document.printed_total,
                request_part=item.request_part,
                title=item.title or None,
                page_indexes=listed_pages or None,
                toc_entries=deduplicate_toc(entries),
            )
        )
        logger.info(
            "Part %s (%s): global pages %s-%s, %s available pages, %s TOC entries",
            item.part,
            item.request_part,
            item.start_page,
            end_page,
            len(listed_pages) if listed_pages else end_page - item.start_page + 1,
            len(entries),
        )

    if not book_title:
        raise RuntimeError("Book title was absent from all discovered volume pages")
    discovery = Discovery(
        book_id=book_id,
        book_title=book_title,
        source_url=source_url,
        part_count=len(parts),
        parts=parts,
        discovered_at=utc_now(),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "discovery.json").write_text(
        json.dumps(discovery.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return discovery


def find_trailing_part_page(
    client: FetchClient,
    *,
    book_id: int,
    request_part: str,
    internal_part: int,
    declared_end: int,
    next_start: int,
) -> int:
    gap = next_start - declared_end - 1
    if gap > 20:
        raise RuntimeError(
            f"Part {request_part!r} has an unexpectedly large metadata gap of {gap} pages"
        )
    for page_index in range(next_start - 1, declared_end, -1):
        result = client.fetch(reader_url(book_id, request_part, page_index))
        document = parse_document(result.text)
        if any(
            page.page_index == page_index
            and page.part == internal_part
            and page.paragraphs
            and page.clean_text
            for page in document.pages
        ):
            return page_index
    return declared_end


def deduplicate_toc(entries: list[TocEntry]) -> list[TocEntry]:
    seen: set[tuple[object, ...]] = set()
    result: list[TocEntry] = []
    for entry in entries:
        key = (entry.part, entry.page_index, entry.title, entry.index_id, entry.parent_index_id)
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return sorted(result, key=lambda entry: (entry.page_index, entry.title))


def build_fragment(
    page: ParsedPage,
    document: ParsedDocument,
    discovery: Discovery,
    *,
    retrieved_at: str,
) -> Fragment:
    part_range = discovery.part_for_page(page.page_index)
    part = part_range.part
    relevant = [entry for entry in part_range.toc_entries if entry.page_index <= page.page_index]
    latest_page = max((entry.page_index for entry in relevant), default=None)
    latest = [entry for entry in relevant if entry.page_index == latest_page]
    is_part_start = page.page_index == part_range.start_page
    part_titles = [part_range.title] if is_part_start and part_range.title else []
    document_titles = [] if is_part_start else title_sections(document.title, discovery.book_title)
    breadcrumb_titles = [] if is_part_start else document.breadcrumb_titles
    section_names: list[str] = []
    for candidate in (
        document_titles
        + part_titles
        + breadcrumb_titles
        + [entry.title for entry in latest]
        + page.headings
    ):
        if candidate and candidate not in section_names:
            section_names.append(candidate)
    index_ids: list[str] = []
    for entry in latest:
        for value in (entry.parent_index_id, entry.index_id):
            if value and value not in index_ids:
                index_ids.append(value)
    toc_path = None if is_part_start else (document.active_index_path or "/".join(index_ids) or None)
    fragment_id = fragment_identifier(discovery.book_id, part, page.page_index)
    previous_id = None
    previous_page = discovery.previous_page(page.page_index)
    if previous_page is not None:
        previous_part = discovery.part_for_page(previous_page).part
        previous_id = fragment_identifier(discovery.book_id, previous_part, previous_page)
    next_id = None
    next_page = discovery.next_page(page.page_index)
    if next_page is not None:
        next_part = discovery.part_for_page(next_page).part
        next_id = fragment_identifier(discovery.book_id, next_part, next_page)
    raw_text = page.raw_text
    clean_text = page.clean_text
    return Fragment(
        fragment_id=fragment_id,
        book_id=discovery.book_id,
        book_title=discovery.book_title,
        part=part,
        index=page.page_index,
        page_index=page.page_index,
        page_number=page.page_number,
        ordinal=discovery.ordinal_for_page(page.page_index),
        section_titles=section_names,
        raw_text=raw_text,
        clean_text=clean_text,
        source_url=reader_url(
            discovery.book_id, part_range.request_value, page.page_index, toc_path
        ),
        toc_index_path=toc_path,
        previous_fragment_id=previous_id,
        next_fragment_id=next_id,
        retrieved_at=retrieved_at,
        checksum_sha256=sha256_text(raw_text),
        first_paragraph_id=page.paragraphs[0].paragraph_id if page.paragraphs else None,
        last_paragraph_id=page.paragraphs[-1].paragraph_id if page.paragraphs else None,
        paragraphs=page.paragraphs,
    )


def fragment_identifier(book_id: int, part: int, page_index: int) -> str:
    return f"{book_id}:{part}:{page_index}"


class DownloadRunner:
    def __init__(
        self,
        *,
        client: FetchClient,
        discovery: Discovery,
        store: FragmentStore,
        output_dir: Path,
        workers: int = 2,
    ) -> None:
        self.client = client
        self.discovery = discovery
        self.store = store
        self.output_dir = output_dir
        self.workers = max(1, min(4, workers))
        self.stop_requested = False
        self.logger = logging.getLogger(__name__)
        self._thread_state = threading.local()

    def install_signal_handlers(self) -> None:
        def request_stop(signum: int, frame: object) -> None:
            self.stop_requested = True
            self.logger.warning("Stop requested; finishing the current response before exiting")

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)

    def run(self) -> int:
        self.install_signal_handlers()
        saved = self.store.saved_pages()
        self.logger.info("Downloader workers: %s", self.workers)
        if self.workers == 1:
            return self._run_sequential(saved)

        page_iter = self._missing_pages(saved)
        executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="ketab-fetch")
        active: dict[Future[PageFetch], tuple[PartRange, int, str]] = {}

        try:
            self._fill_queue(executor, active, page_iter, saved)
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    part_range, page, url = active.pop(future)
                    try:
                        fetched = future.result()
                        self._save_fetch(fetched, saved)
                    except Exception as exc:
                        self.store.record_failure(part_range.part, page, url, str(exc), utc_now())
                        self.logger.error("Stopped at part %s page %s: %s", part_range.part, page, exc)
                        for pending in active:
                            pending.cancel()
                        raise
                if not self.stop_requested:
                    self._fill_queue(executor, active, page_iter, saved)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        return self.store.count()

    def _run_sequential(self, saved: set[int]) -> int:
        for part_range in self.discovery.parts:
            for page in tuple(part_range.pages):
                if self.stop_requested:
                    break
                if page in saved:
                    continue
                url = reader_url(self.discovery.book_id, part_range.request_value, page)
                try:
                    fetched = self._fetch_page_with_client(self.client, part_range, page, url)
                    self._save_fetch(fetched, saved)
                except Exception as exc:
                    self.store.record_failure(part_range.part, page, url, str(exc), utc_now())
                    self.logger.error("Stopped at part %s page %s: %s", part_range.part, page, exc)
                    raise
            if self.stop_requested:
                break
        return self.store.count()

    def _missing_pages(self, saved: set[int]):
        for part_range in self.discovery.parts:
            for page in tuple(part_range.pages):
                if self.stop_requested:
                    return
                if page not in saved:
                    yield part_range, page

    def _fill_queue(
        self,
        executor: ThreadPoolExecutor,
        active: dict[Future[PageFetch], tuple[PartRange, int, str]],
        page_iter,
        saved: set[int],
    ) -> None:
        while not self.stop_requested and len(active) < self.workers:
            try:
                part_range, page = next(page_iter)
            except StopIteration:
                return
            if page in saved:
                continue
            url = reader_url(self.discovery.book_id, part_range.request_value, page)
            future = executor.submit(self._fetch_page, part_range, page, url)
            active[future] = (part_range, page, url)

    def _fetch_page(self, part_range: PartRange, page: int, url: str) -> PageFetch:
        client = self._thread_client()
        return self._fetch_page_with_client(client, part_range, page, url)

    def _fetch_page_with_client(
        self,
        client: FetchClient,
        part_range: PartRange,
        page: int,
        url: str,
    ) -> PageFetch:
        semantic_attempts = int(getattr(client, "max_retries", 0)) + 1
        for attempt in range(semantic_attempts):
            result = client.fetch(url)
            document = parse_document(result.text)
            matching = [
                candidate
                for candidate in document.pages
                if part_range.contains(candidate.page_index)
            ]
            if any(candidate.page_index == page for candidate in matching):
                return PageFetch(
                    part_range=part_range,
                    requested_page=page,
                    url=url,
                    response_text=result.text,
                    document=document,
                    matching=matching,
                    retrieved_at=utc_now(),
                )

            listed_without_text = any(
                listed.page_index == page
                and listed.part == part_range.part
                and listed.request_part == part_range.request_value
                for listed in document.volume_pages
            )
            if not listed_without_text or attempt + 1 == semantic_attempts:
                return PageFetch(
                    part_range=part_range,
                    requested_page=page,
                    url=url,
                    response_text=result.text,
                    document=document,
                    matching=matching,
                    retrieved_at=utc_now(),
                    confirmed_empty=listed_without_text,
                )
            self.logger.warning(
                "Page %s is listed by the server but has no text; semantic retry %s/%s",
                page,
                attempt + 1,
                semantic_attempts,
            )

        raise AssertionError("semantic retry loop did not return")

    def _thread_client(self) -> FetchClient:
        client = getattr(self._thread_state, "client", None)
        if client is None:
            client = self.client.clone()
            self._thread_state.client = client
        return client

    def _save_fetch(self, fetched: PageFetch, saved: set[int]) -> None:
        if fetched.confirmed_empty:
            self._exclude_confirmed_empty_page(fetched)
            return
        if not any(candidate.page_index == fetched.requested_page for candidate in fetched.matching):
            problem = save_problem_response(
                self.output_dir / "problem_pages",
                fetched.part_range.part,
                fetched.requested_page,
                fetched.response_text,
            )
            raise RuntimeError(f"Expected page {fetched.requested_page} was absent; response saved to {problem}")

        for parsed_page in fetched.matching:
            if parsed_page.page_index in saved:
                continue
            fragment = build_fragment(parsed_page, fetched.document, self.discovery, retrieved_at=fetched.retrieved_at)
            if not fragment.paragraphs or not fragment.clean_text:
                problem = save_problem_response(
                    self.output_dir / "problem_pages",
                    fetched.part_range.part,
                    parsed_page.page_index,
                    fetched.response_text,
                )
                raise RuntimeError(f"Empty parsed page; response saved to {problem}")
            if self.store.save_fragment(fragment):
                saved.add(parsed_page.page_index)
                self.logger.info(
                    "Saved fragment %s (%s/%s)",
                    fragment.fragment_id,
                    len(saved),
                    self.discovery.fragment_count,
                )

    def _exclude_confirmed_empty_page(self, fetched: PageFetch) -> None:
        problem = save_problem_response(
            self.output_dir / "problem_pages",
            fetched.part_range.part,
            fetched.requested_page,
            fetched.response_text,
        )
        if not fetched.part_range.exclude_page(fetched.requested_page):
            raise RuntimeError(
                f"Could not exclude confirmed empty page {fetched.requested_page}; "
                f"response saved to {problem}"
            )
        discovery_path = self.output_dir / "discovery.json"
        discovery_path.write_text(
            json.dumps(self.discovery.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        self.store.save_discovery(self.discovery)
        self.logger.warning(
            "Skipped confirmed empty page %s in part %s; map updated (%s text pages remain)",
            fetched.requested_page,
            fetched.part_range.part,
            self.discovery.fragment_count,
        )


def run_test_download(
    *,
    client: FetchClient,
    discovery: Discovery,
    output_dir: Path,
) -> tuple[list[Fragment], set[int]]:
    test_dir = output_dir / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    requested = test_sample_pages(discovery)
    collected: dict[int, Fragment] = {}
    logger = logging.getLogger(__name__)
    for page_index in sorted(requested):
        if page_index in collected:
            continue
        part = discovery.part_for_page(page_index).part
        result = client.fetch(
            reader_url(discovery.book_id, discovery.part_for_page(page_index).request_value, page_index)
        )
        document = parse_document(result.text)
        matching = [page for page in document.pages if discovery.first_page <= page.page_index <= discovery.last_page]
        if not any(page.page_index == page_index for page in matching):
            problem = save_problem_response(test_dir / "problem_pages", part, page_index, result.text)
            raise RuntimeError(f"Control page {page_index} was absent; response saved to {problem}")
        retrieved_at = utc_now()
        for page in matching:
            if page.page_index in requested:
                collected[page.page_index] = build_fragment(page, document, discovery, retrieved_at=retrieved_at)
        logger.info("Checked requested page %s; parsed %s embedded page(s)", page_index, len(matching))
    fragments = [collected[index] for index in sorted(collected)]
    missing = requested - set(collected)
    if missing:
        raise RuntimeError(f"Test did not collect requested control pages: {sorted(missing)}")
    jsonl = "\n".join(json.dumps(fragment.to_dict(), ensure_ascii=False, sort_keys=True) for fragment in fragments)
    (test_dir / "fragments.jsonl").write_text(jsonl + "\n", encoding="utf-8", newline="\n")
    return fragments, requested


def test_sample_pages(discovery: Discovery) -> set[int]:
    samples = {
        discovery.first_page,
        min(discovery.first_page + 1, discovery.last_page),
        (discovery.first_page + discovery.last_page) // 2,
        max(discovery.first_page, discovery.last_page - 1),
        discovery.last_page,
    }
    for part in discovery.parts:
        samples.add(part.pages[0])
        samples.add(part.pages[-1])
        samples.add(part.pages[len(part.pages) // 2])
        previous = discovery.previous_page(part.start_page)
        if previous is not None:
            samples.add(previous)
    return samples & discovery.expected_pages
