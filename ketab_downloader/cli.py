from __future__ import annotations

import argparse
import json
import logging
import sys
import tomllib
from pathlib import Path

from .client import FetchClient
from .exporter import export_book
from .models import Discovery, PartRange, TocEntry
from .storage import FragmentStore
from .validation import validate_fragments, write_validation_report
from .workflow import DownloadRunner, discover_book, reader_url, run_test_download


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Последовательное сохранение структурированного текста книг KetabOnline"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="URL страницы чтения KetabOnline")
    source.add_argument("--book-id", type=int, help="ID книги KetabOnline")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test", action="store_true", help="скачать только распределённую контрольную выборку")
    mode.add_argument("--validate-only", action="store_true", help="проверить уже сохранённую основную базу")
    mode.add_argument("--export", action="store_true", help="проверить и экспортировать уже сохранённую основную базу")
    parser.add_argument("--delay", type=float, help="пауза между запросами в секундах")
    parser.add_argument("--timeout", type=float, help="сетевой тайм-аут в секундах")
    parser.add_argument("--max-retries", type=int, help="число повторов временных ошибок")
    parser.add_argument("--workers", type=int, help="число одновременных запросов для полной загрузки (1-4)")
    parser.add_argument("--restart", action="store_true", help="очистить основную SQLite-базу перед полной загрузкой")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="путь к TOML-конфигурации")
    parser.add_argument("--output-dir", type=Path, help="корневая папка результатов")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    network = config.get("network", {})
    output_config = config.get("output", {})
    book_id = args.book_id or book_id_from_reader_url(args.url)
    source_url = args.url or reader_url(book_id, 1, 0)
    output_root = args.output_dir or Path(output_config.get("directory", "output"))
    output_dir = output_root / str(book_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(output_dir / "download.log")
    logger = logging.getLogger(__name__)
    workers = args.workers if args.workers is not None else int(network.get("workers", 2))
    if workers < 1:
        logger.warning("Workers value %s is too low; using 1", workers)
        workers = 1
    elif workers > 4:
        logger.warning("Workers value %s is too high; using 4", workers)
        workers = 4

    discovery_path = output_dir / "discovery.json"
    offline_mode = args.validate_only or args.export
    if offline_mode:
        if not discovery_path.exists():
            logger.error("Нет %s; сначала выполните исследование или тест", discovery_path)
            return 2
        discovery = discovery_from_dict(json.loads(discovery_path.read_text(encoding="utf-8")))
    else:
        client = FetchClient(
            delay=args.delay if args.delay is not None else float(network.get("delay", 1.5)),
            timeout=args.timeout if args.timeout is not None else float(network.get("timeout", 30.0)),
            max_retries=args.max_retries if args.max_retries is not None else int(network.get("max_retries", 4)),
            backoff_base=float(network.get("backoff_base", 1.0)),
            user_agent=str(
                network.get(
                    "user_agent",
                    "KetabOnlineStructuredArchiver/0.1 (+personal archival; limited concurrent requests)",
                )
            ),
        )
        if discovery_path.exists() and not args.test and not args.restart:
            discovery = discovery_from_dict(json.loads(discovery_path.read_text(encoding="utf-8")))
            logger.info("Используется сохранённая карта книги; повторное исследование не требуется")
        else:
            discovery = discover_book(client, book_id=book_id, source_url=source_url, output_dir=output_dir)

    if args.test:
        fragments, requested = run_test_download(client=client, discovery=discovery, output_dir=output_dir)
        validation = validate_fragments(fragments, discovery, expected_pages=requested)
        test_dir = output_dir / "test"
        write_test_preview(test_dir / "preview.md", fragments, discovery)
        write_validation_report(
            test_dir / "validation_report.md",
            validation,
            fragments,
            discovery,
            test_mode=True,
        )
        logger.info("Test saved %s control fragments", len(fragments))
        return 0 if validation.ok else 1

    database_path = output_dir / "fragments.sqlite3"
    with FragmentStore(database_path) as store:
        if args.restart:
            store.clear()
        store.save_discovery(discovery)

        if args.validate_only or args.export:
            fragments = store.fragments()
            validation = validate_fragments(fragments, discovery, require_complete=True)
            write_validation_report(
                output_dir / "validation_report.md",
                validation,
                fragments,
                discovery,
                test_mode=False,
            )
            if not validation.ok:
                logger.error("Проверка не пройдена; экспорт остановлен")
                return 1
            if args.export:
                export_book(output_dir, fragments, discovery, failures=store.failures())
            return 0

        runner = DownloadRunner(
            client=client,
            discovery=discovery,
            store=store,
            output_dir=output_dir,
            workers=workers,
        )
        count = runner.run()
        fragments = store.fragments()
        complete = count == discovery.fragment_count
        validation = validate_fragments(fragments, discovery, require_complete=complete)
        write_validation_report(
            output_dir / "validation_report.md",
            validation,
            fragments,
            discovery,
            test_mode=False,
        )
        if complete and validation.ok:
            export_book(output_dir, fragments, discovery, failures=store.failures())
            logger.info("Полная выгрузка завершена и экспортирована")
            return 0
        logger.warning("Сохранено %s из %s; повторный запуск автоматически продолжит работу", count, discovery.fragment_count)
        return 130 if runner.stop_requested else 1


def load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def setup_logging(path: Path) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)


def discovery_from_dict(data: dict[str, object]) -> Discovery:
    parts: list[PartRange] = []
    for raw_part in data["parts"]:  # type: ignore[index]
        item = dict(raw_part)
        entries = [TocEntry(**entry) for entry in item.pop("toc_entries", [])]
        item.pop("fragment_count", None)
        parts.append(PartRange(toc_entries=entries, **item))
    return Discovery(
        book_id=int(data["book_id"]),
        book_title=str(data["book_title"]),
        source_url=str(data["source_url"]),
        part_count=int(data["part_count"]),
        parts=parts,
        discovered_at=str(data["discovered_at"]),
    )


def write_test_preview(path: Path, fragments: list, discovery: Discovery) -> None:
    lines = [
        f"# Контрольная выборка: {discovery.book_title}",
        "",
        f"Проверено фрагментов: {len(fragments)}.",
        "",
    ]
    for fragment in sorted(fragments, key=lambda item: item.page_index):
        raw_sample = fragment.raw_text[:320].replace("\n", " ")
        clean_sample = fragment.clean_text[:320].replace("\n", " ")
        lines.extend(
            [
                f"## `{fragment.fragment_id}` — том {fragment.part}, страница {fragment.page_number}",
                "",
                f"Источник: {fragment.source_url}",
                "",
                f"Заголовки: {json.dumps(fragment.section_titles, ensure_ascii=False)}",
                "",
                "**Raw:**",
                "",
                f"> {raw_sample}",
                "",
                "**Clean:**",
                "",
                f"> {clean_sample}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def book_id_from_reader_url(url: str | None) -> int:
    if not url:
        raise ValueError("URL is required")
    parts = [part for part in url.split("?")[0].split("/") if part]
    try:
        return int(parts[parts.index("books") + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Не удалось определить ID книги из URL: {url}") from exc


if __name__ == "__main__":
    sys.exit(main())
