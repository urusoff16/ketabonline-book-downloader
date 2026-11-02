from __future__ import annotations

import unittest
from pathlib import Path

from ketab_downloader.parser import diacritic_count, parse_document


FIXTURE = Path(__file__).parent / "fixtures" / "reader_sample.html"


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = parse_document(FIXTURE.read_text(encoding="utf-8"))

    def test_preserves_arabic_unicode_and_diacritics(self) -> None:
        self.assertEqual(self.document.book_title, "كتاب الاختبار")
        self.assertEqual(len(self.document.pages), 2)
        text = self.document.pages[0].raw_text
        self.assertIn("بِسْمِ اللَّهِ", text)
        self.assertGreater(diacritic_count(text), 10)

    def test_extracts_paragraph_navigation_without_interface_icon(self) -> None:
        paragraph = self.document.pages[0].paragraphs[0]
        self.assertEqual(paragraph.paragraph_id, "p-77-0-1")
        self.assertEqual(paragraph.next_data_id, "p-77-0-2")
        self.assertEqual(paragraph.local_index, 1)
        self.assertNotIn("ico", paragraph.raw_text)

    def test_extracts_page_and_toc_metadata(self) -> None:
        self.assertEqual(self.document.volume_numbers, [1, 2])
        self.assertEqual(self.document.slider_max, 8)
        self.assertEqual(self.document.printed_total, 10)
        self.assertEqual(self.document.pages[0].headings, ["[بَابُ الطَّهَارَةِ]"])
        self.assertEqual(self.document.toc_entries[0].index_id, "9001")
        self.assertEqual(self.document.toc_entries[1].parent_index_id, "9001")

    def test_extracts_page_from_nuxt_page_in_view(self) -> None:
        content = (
            '<p class=\\"g-paragraph\\" id=\\"p-77-4-1\\"> نَصٌّ مُشَكَّلٌ '
            '<a class=\\"g-copy\\" data-book-id=\\"77\\" data-page-id=\\"4\\" '
            'data-number=\\"6\\" data-part-name=\\"1\\" data-index=\\"1\\" '
            'data-id=\\"p-77-4-1\\" next-data-id=\\"p-77-4-2\\"></a></p>'
        )
        html = (
            '<html><head><title>كتاب الاختبار | صفحة 6</title></head><body>'
            '<script>window.__NUXT__=(function(a,b){return {data:[{pageInView:{id:a,page:b,'
            'book:{id:77,title:"كتاب الاختبار",lang:"ar"},seal:"X",content:"'
            + content
            + '",quran:null,breadcrumb:[{id:9001,page_id:a,title:"باب الاختبار"}],index:9001}}]}}(4,6));</script></body></html>'
        )
        document = parse_document(html)
        self.assertEqual(len(document.pages), 1)
        self.assertEqual(document.pages[0].page_index, 4)
        self.assertIn("نَصٌّ مُشَكَّلٌ", document.pages[0].raw_text)
        self.assertEqual(document.breadcrumb_titles, ["باب الاختبار"])
        self.assertEqual(document.active_index_path, "9001")

    def test_extracts_textual_introduction_and_numeric_volume_metadata(self) -> None:
        html = (
            '<html><body><script>window.__NUXT__=(function(a,b,c,d,e,f,g,h,i,j,k){return '
            '{data:[{book:{parts:[{title:a,page_id:b,pages:c,name:d},'
            '{title:e,page_id:f,pages:g,name:h}],pages_list:['
            '{id:b,page:b,part:d},{id:i,page:b,part:d},{id:j,page:c,part:d},'
            '{id:f,page:b,part:h},{id:k,page:g,part:h}]}}]}}'
            '("الأصل - مقدمة التحقيق",1,311,"مقدمة",'
            '"الأصل - ج 1: الصلاة",315,372,"1",7,313,686));</script></body></html>'
        )
        document = parse_document(html)
        self.assertEqual(
            [
                (part.part, part.request_part, part.start_page, part.page_count)
                for part in document.volume_parts
            ],
            [(0, "مقدمة", 1, 311), (1, "1", 315, 372)],
        )
        self.assertEqual(
            [(page.part, page.request_part, page.page_index) for page in document.volume_pages],
            [
                (0, "مقدمة", 1),
                (0, "مقدمة", 7),
                (0, "مقدمة", 313),
                (1, "1", 315),
                (1, "1", 686),
            ],
        )

    def test_toc_accepts_textual_introduction_part(self) -> None:
        html = (
            '<a class="BookSideMenuItem" '
            'href="/ar/books/1909/read?part=مقدمة&amp;page=1" rel="المقدمة">'
            'المقدمة</a>'
        )
        document = parse_document(html)
        self.assertEqual(len(document.toc_entries), 1)
        self.assertEqual(document.toc_entries[0].part, 0)


if __name__ == "__main__":
    unittest.main()
