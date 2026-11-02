from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ketab_downloader.client import FetchResult
from ketab_downloader.models import Discovery, PartRange
from ketab_downloader.storage import FragmentStore
from ketab_downloader.workflow import (
    DownloadRunner,
    discover_book,
    normalize_source_url,
    reader_url,
)


class ConcurrentFakeClient:
    def __init__(self, html: str) -> None:
        self.html = html
        self.current = 0
        self.max_current = 0
        self.lock = threading.Lock()

    def clone(self) -> "ConcurrentFakeClient":
        return self

    def fetch(self, url: str) -> FetchResult:
        with self.lock:
            self.current += 1
            self.max_current = max(self.max_current, self.current)
        try:
            time.sleep(0.03)
            return FetchResult(url=url, status=200, text=self.html, content_type="text/html")
        finally:
            with self.lock:
                self.current -= 1


VOLUME_METADATA = (
    '<script>window.__NUXT__=(function(a,b,c,d,e,f,g,h,i,j,k){return '
    '{data:[{book:{parts:[{title:a,page_id:b,pages:c,name:d},'
    '{title:e,page_id:f,pages:g,name:h}],pages_list:['
    '{id:b,page:b,part:d},{id:i,page:b,part:d},{id:j,page:c,part:d},'
    '{id:f,page:b,part:h},{id:k,page:g,part:h}]}}]}}'
    '("الأصل - مقدمة التحقيق",1,311,"مقدمة",'
    '"الأصل - ج 1: الصلاة",315,372,"1",7,313,686));</script>'
)


def metadata_page_html(page: int | None, part: str, slider_max: int) -> str:
    page_html = ""
    if page is not None:
        page_html = (
            f'<div book-page-index="{page}" class="book-page">'
            f'<p class="g-paragraph" id="p-1909-{page}-1">نَصٌّ الصفحة {page}'
            f'<a class="g-copy" data-book-id="1909" data-page-id="{page}" '
            f'data-number="{page}" data-part-name="{part}" data-index="1" '
            f'data-id="p-1909-{page}-1"></a></p></div>'
        )
    return (
        '<html><head><title>الأصل للشيباني (ط. قطر) | جامع الكتب الإسلامية</title></head>'
        f'<body><input type="range" max="{slider_max}" value="0">'
        f'{page_html}{VOLUME_METADATA}</body></html>'
    )


class MetadataFakeClient:
    def fetch(self, url: str) -> FetchResult:
        query = parse_qs(urlparse(url).query)
        part = query.get("part", ["1"])[0]
        page = int(query.get("page", ["0"])[0])
        if page == 0:
            html = metadata_page_html(None, "1", 371)
        elif page in {1, 313}:
            html = metadata_page_html(page, "مقدمة", 310)
        elif page == 314:
            html = metadata_page_html(None, "مقدمة", 310)
        elif page == 315:
            html = metadata_page_html(page, "1", 371)
        else:
            raise AssertionError(f"Unexpected metadata request: part={part}, page={page}")
        return FetchResult(url=url, status=200, text=html, content_type="text/html")


EMPTY_PAGE_METADATA = (
    '<script>window.__NUXT__=(function(a,b,c,d,e){return '
    '{data:[{book:{parts:[{title:a,page_id:b,pages:c,name:d}],pages_list:['
    '{id:b,page:b,part:d},{id:e,page:c,part:d}]}}]}}'
    '("المقدمة",6,2,"مقدمة",7));</script>'
)


class SemanticEmptyClient:
    max_retries = 2

    def __init__(self) -> None:
        self.requested_pages: list[int] = []

    def fetch(self, url: str) -> FetchResult:
        page = int(parse_qs(urlparse(url).query)["page"][0])
        self.requested_pages.append(page)
        page_html = ""
        if page == 7:
            page_html = (
                '<div book-page-index="7" class="book-page">'
                '<p class="g-paragraph" id="p-1909-7-1">نَصٌّ الصفحة 7'
                '<a class="g-copy" data-book-id="1909" data-page-id="7" '
                'data-number="7" data-part-name="مقدمة" data-index="1" '
                'data-id="p-1909-7-1"></a></p></div>'
            )
        html = f"<html><body>{page_html}{EMPTY_PAGE_METADATA}</body></html>"
        return FetchResult(url=url, status=200, text=html, content_type="text/html")


class WorkflowTests(unittest.TestCase):
    def test_textual_part_url_and_non_contiguous_page_navigation(self) -> None:
        introduction = PartRange(
            part=0,
            start_page=1,
            end_page=313,
            local_slider_max=310,
            printed_total=None,
            request_part="مقدمة",
            page_indexes=[1, 2, 3, 4, 5, 7, 313],
        )
        volume_one = PartRange(
            part=1,
            start_page=315,
            end_page=316,
            local_slider_max=1,
            printed_total=None,
            request_part="1",
            page_indexes=[315, 316],
        )
        discovery = Discovery(
            book_id=1909,
            book_title="fixture",
            source_url=reader_url(1909, "مقدمة", 1),
            part_count=2,
            parts=[introduction, volume_one],
            discovered_at="2026-07-22T00:00:00+00:00",
        )
        self.assertNotIn(6, discovery.expected_pages)
        self.assertNotIn(314, discovery.expected_pages)
        self.assertEqual(discovery.next_page(5), 7)
        self.assertEqual(discovery.previous_page(7), 5)
        self.assertEqual(discovery.next_page(313), 315)
        self.assertEqual(discovery.previous_page(315), 313)
        self.assertEqual(discovery.ordinal_for_page(315), 8)
        self.assertIn("part=%D9%85%D9%82%D8%AF%D9%85%D8%A9", discovery.source_url)
        self.assertEqual(
            normalize_source_url(
                "https://ketabonline.com/ar/books/1909/read?part=مقدمة&page=1",
                1909,
            ),
            discovery.source_url,
        )

    def test_discovery_uses_nuxt_volume_map_and_skips_blank_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            discovery = discover_book(
                MetadataFakeClient(),
                book_id=1909,
                source_url=reader_url(1909, 1, 0),
                output_dir=Path(directory),
            )
        self.assertEqual(discovery.part_count, 2)
        self.assertEqual(
            [
                (part.part, part.request_value, part.start_page, part.end_page)
                for part in discovery.parts
            ],
            [(0, "مقدمة", 1, 313), (1, "1", 315, 686)],
        )
        self.assertEqual(discovery.parts[0].page_indexes, [1, 7, 313])
        self.assertEqual(discovery.parts[1].page_indexes, [315, 686])
        self.assertNotIn(6, discovery.expected_pages)
        self.assertNotIn(314, discovery.expected_pages)

    def test_full_download_uses_limited_parallel_fetches(self) -> None:
        fixture = Path("tests/fixtures/reader_sample.html").read_text(encoding="utf-8")
        discovery = Discovery(
            book_id=77,
            book_title="fixture",
            source_url="https://ketabonline.com/ar/books/77/read?part=1&page=0",
            part_count=1,
            parts=[PartRange(part=1, start_page=0, end_page=1, local_slider_max=1, printed_total=10)],
            discovered_at="2026-07-17T00:00:00+00:00",
        )
        client = ConcurrentFakeClient(fixture)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with FragmentStore(output_dir / "fragments.sqlite3") as store:
                runner = DownloadRunner(
                    client=client,
                    discovery=discovery,
                    store=store,
                    output_dir=output_dir,
                    workers=2,
                )
                count = runner.run()

        self.assertEqual(count, 2)
        self.assertEqual(client.max_current, 2)

    def test_repeated_server_listed_empty_page_is_excluded_and_download_continues(self) -> None:
        part = PartRange(
            part=0,
            start_page=6,
            end_page=7,
            local_slider_max=1,
            printed_total=None,
            request_part="مقدمة",
            page_indexes=[6, 7],
        )
        discovery = Discovery(
            book_id=1909,
            book_title="fixture",
            source_url=reader_url(1909, "مقدمة", 6),
            part_count=1,
            parts=[part],
            discovered_at="2026-07-22T00:00:00+00:00",
        )
        client = SemanticEmptyClient()

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with FragmentStore(output_dir / "fragments.sqlite3") as store:
                runner = DownloadRunner(
                    client=client,
                    discovery=discovery,
                    store=store,
                    output_dir=output_dir,
                    workers=1,
                )
                count = runner.run()
            saved_map = (output_dir / "discovery.json").read_text(encoding="utf-8")

        self.assertEqual(client.requested_pages, [6, 6, 6, 7])
        self.assertEqual(count, 1)
        self.assertEqual(part.page_indexes, [7])
        self.assertEqual(part.excluded_pages, [6])
        self.assertIn('"excluded_pages": [', saved_map)


if __name__ == "__main__":
    unittest.main()
