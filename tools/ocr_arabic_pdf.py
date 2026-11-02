from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence


FORMAT_VERSION = "arabic-pdf-ocr-v1"
MAX_PAGES_WITHOUT_CONFIRMATION = 25
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PAGE_TOKEN_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
NUMERIC_LINE_RE = re.compile(r"^[\s\[\](){}<>«»﴾﴿]*([0-9٠-٩۰-۹]{1,4})[\s\[\](){}<>«»﴾﴿.،,:؛-]*$")
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")


class OcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], int], CommandResult]


@dataclass(frozen=True)
class Tools:
    pdftoppm: Path
    tesseract: Path
    tessdata_dir: Path
    pdftoppm_version: str
    tesseract_version: str
    traineddata_sha256: dict[str, str]


@dataclass(frozen=True)
class Settings:
    dpi: int
    language: str
    psm: int
    oem: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "dpi": self.dpi,
            "language": self.language,
            "oem": self.oem,
            "psm": self.psm,
        }


@dataclass(frozen=True)
class PageResult:
    pdf_page: int
    record: dict[str, Any] | None
    cached: bool
    error: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run local Poppler + Tesseract OCR for explicitly selected PDF pages. "
            "There is deliberately no implicit all-pages mode."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Scanned PDF file")
    parser.add_argument(
        "--pages",
        required=True,
        help="Required 1-based PDF pages, for example: 10,94,188,281,373",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for cached images, OCR text, JSONL, manifest and report",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--lang", default="ara", help="Tesseract language(s)")
    parser.add_argument("--psm", type=int, default=3, help="Tesseract page segmentation mode")
    parser.add_argument("--oem", type=int, default=1, help="Tesseract OCR engine mode")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent local OCR jobs")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds per external command")
    parser.add_argument("--pdftoppm", type=Path, help="Explicit pdftoppm executable")
    parser.add_argument("--tesseract", type=Path, help="Explicit Tesseract executable")
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        help="Tesseract data directory; auto-detects runtime/ocr/tessdata",
    )
    parser.add_argument(
        "--allow-many-pages",
        action="store_true",
        help=f"Required when selecting more than {MAX_PAGES_WITHOUT_CONFIRMATION} pages",
    )
    parser.add_argument("--force", action="store_true", help="Ignore valid page cache")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pages = parse_pages(args.pages)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"--pages: {exc}") from exc
    validate_options(args)
    enforce_page_limit(pages, args.allow_many_pages)

    input_pdf = args.input.resolve()
    if not input_pdf.is_file():
        raise SystemExit(f"Input PDF does not exist: {input_pdf}")
    if input_pdf.suffix.casefold() != ".pdf":
        raise SystemExit(f"Input must be a PDF file: {input_pdf}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(args.dpi, args.lang, args.psm, args.oem)

    try:
        tools = discover_tools(
            args.pdftoppm,
            args.tesseract,
            args.tessdata_dir,
            args.lang,
            args.timeout,
        )
        source_sha256 = file_sha256(input_pdf)
        results = run_selected_pages(
            input_pdf=input_pdf,
            output_dir=output_dir,
            pages=pages,
            source_sha256=source_sha256,
            settings=settings,
            tools=tools,
            workers=args.workers,
            timeout=args.timeout,
            force=args.force,
        )
        write_aggregate_outputs(
            input_pdf=input_pdf,
            output_dir=output_dir,
            pages=pages,
            source_sha256=source_sha256,
            settings=settings,
            tools=tools,
            results=results,
        )
    except OcrError as exc:
        raise SystemExit(str(exc)) from exc

    failed = [result for result in results if result.error]
    cached = sum(result.cached for result in results)
    print(
        f"OCR finished: {len(results) - len(failed)}/{len(results)} pages successful, "
        f"{cached} from cache. Output: {output_dir}"
    )
    return 1 if failed else 0


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for raw_token in value.split(","):
        token = raw_token.strip()
        match = PAGE_TOKEN_RE.fullmatch(token)
        if not match:
            raise argparse.ArgumentTypeError(
                f"Invalid page token {token!r}; use comma-separated numbers or ranges"
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise argparse.ArgumentTypeError(f"Invalid page range: {token}")
        if end - start > 10_000:
            raise argparse.ArgumentTypeError(f"Page range is unreasonably large: {token}")
        pages.update(range(start, end + 1))
    if not pages:
        raise argparse.ArgumentTypeError("--pages must contain at least one page")
    return sorted(pages)


def validate_options(args: argparse.Namespace) -> None:
    if args.dpi < 72 or args.dpi > 1200:
        raise SystemExit("--dpi must be between 72 and 1200")
    if args.psm < 0 or args.psm > 13:
        raise SystemExit("--psm must be between 0 and 13")
    if args.oem < 0 or args.oem > 3:
        raise SystemExit("--oem must be between 0 and 3")
    if args.workers < 1 or args.workers > 16:
        raise SystemExit("--workers must be between 1 and 16")
    if args.timeout < 10:
        raise SystemExit("--timeout must be at least 10 seconds")
    if not args.lang or not LANGUAGE_RE.fullmatch(args.lang):
        raise SystemExit("--lang must contain only Tesseract language identifiers")


def enforce_page_limit(pages: Sequence[int], allow_many_pages: bool) -> None:
    if len(pages) > MAX_PAGES_WITHOUT_CONFIRMATION and not allow_many_pages:
        raise SystemExit(
            f"Refusing to process {len(pages)} pages without --allow-many-pages; "
            f"the safety limit is {MAX_PAGES_WITHOUT_CONFIRMATION}"
        )


def discover_tools(
    explicit_pdftoppm: Path | None,
    explicit_tesseract: Path | None,
    explicit_tessdata_dir: Path | None,
    language: str,
    timeout: int,
    runner: Runner | None = None,
) -> Tools:
    runner = runner or run_command
    pdftoppm = resolve_executable(
        "pdftoppm",
        explicit_pdftoppm,
        bundled_poppler_candidates("pdftoppm"),
    )
    tesseract = resolve_executable(
        "tesseract",
        explicit_tesseract,
        windows_tesseract_candidates(),
    )
    requested_languages = {part for part in language.split("+") if part}
    tessdata_dir = resolve_tessdata_dir(explicit_tessdata_dir, requested_languages)
    traineddata_sha256 = {
        name: file_sha256(tessdata_dir / f"{name}.traineddata")
        for name in sorted(requested_languages)
    }

    pdftoppm_version = first_output_line(
        checked_command(
            [str(pdftoppm), "-v"],
            timeout,
            runner,
            "pdftoppm version",
            merge_stderr=True,
        )
    )
    tesseract_version = first_output_line(
        checked_command([str(tesseract), "--version"], timeout, runner, "Tesseract version")
    )
    languages_output = checked_command(
        [str(tesseract), "--tessdata-dir", str(tessdata_dir), "--list-langs"],
        timeout,
        runner,
        "Tesseract languages",
    )
    installed_languages = {
        line.strip() for line in languages_output.splitlines() if line.strip()
    }
    missing = sorted(requested_languages - installed_languages)
    if missing:
        raise OcrError(
            "Tesseract is missing requested language data: "
            + ", ".join(missing)
            + ". Install the Arabic traineddata file before OCR."
        )
    return Tools(
        pdftoppm,
        tesseract,
        tessdata_dir,
        pdftoppm_version,
        tesseract_version,
        traineddata_sha256,
    )


def resolve_tessdata_dir(
    explicit: Path | None, requested_languages: set[str]
) -> Path:
    if explicit is not None:
        candidates = [explicit.expanduser().resolve()]
    else:
        repository_root = Path(__file__).resolve().parents[1]
        candidates = [repository_root / "runtime" / "ocr" / "tessdata"]
        prefix = os.environ.get("TESSDATA_PREFIX")
        if prefix:
            candidates.append(Path(prefix))
        for executable in windows_tesseract_candidates():
            candidates.append(executable.parent / "tessdata")

    checked: list[str] = []
    for candidate in unique_paths(candidates):
        checked.append(str(candidate))
        if candidate.is_dir() and all(
            (candidate / f"{name}.traineddata").is_file()
            for name in requested_languages
        ):
            return candidate.resolve()
    missing = ", ".join(f"{name}.traineddata" for name in sorted(requested_languages))
    raise OcrError(
        f"Cannot find tessdata directory containing {missing}. Checked: "
        + "; ".join(checked)
    )


def resolve_executable(name: str, explicit: Path | None, candidates: Sequence[Path]) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise OcrError(f"{name} executable does not exist: {path}")
        return path

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise OcrError(
        f"Cannot find {name}. Install it or pass --{name.replace('_', '-')} PATH."
    )


def bundled_poppler_candidates(program: str) -> list[Path]:
    roots: list[Path] = []
    executable = Path(sys.executable).resolve()
    roots.extend(executable.parents)
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(
            [
                root / "native" / "poppler" / "Library" / "bin" / f"{program}.exe",
                root / "poppler" / "Library" / "bin" / f"{program}.exe",
                root / "native" / "poppler" / "bin" / f"{program}.exe",
                root / "poppler" / "bin" / f"{program}.exe",
                root / "bin" / "override" / f"{program}.exe",
            ]
        )
        for suffix in (f"{program}.cmd", f"{program}.bat"):
            candidates.extend(
                [
                    root / "native" / "poppler" / "bin" / suffix,
                    root / "poppler" / "bin" / suffix,
                    root / "bin" / "override" / suffix,
                ]
            )
    return unique_paths(candidates)


def windows_tesseract_candidates() -> list[Path]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    candidates: list[Path] = []
    for raw_root in roots:
        if not raw_root:
            continue
        root = Path(raw_root)
        candidates.extend(
            [
                root / "Tesseract-OCR" / "tesseract.exe",
                root / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            ]
        )
    return unique_paths(candidates)


def unique_paths(paths: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def run_command(command: Sequence[str], timeout: int) -> CommandResult:
    actual_command: Sequence[str] = command
    executable = Path(command[0])
    if executable.suffix.casefold() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline(list(command))
        actual_command = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            command_line,
        ]
    try:
        completed = subprocess.run(
            list(actual_command),
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrError(f"Could not run {executable.name}: {exc}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def checked_command(
    command: Sequence[str],
    timeout: int,
    runner: Runner,
    purpose: str,
    *,
    merge_stderr: bool = False,
) -> str:
    result = runner(command, timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 2_000:
            detail = detail[-2_000:]
        raise OcrError(f"{purpose} failed (exit {result.returncode}): {detail}")
    if merge_stderr and result.stderr:
        return result.stdout + "\n" + result.stderr
    return result.stdout


def first_output_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "unknown")


def run_selected_pages(
    *,
    input_pdf: Path,
    output_dir: Path,
    pages: Sequence[int],
    source_sha256: str,
    settings: Settings,
    tools: Tools,
    workers: int,
    timeout: int,
    force: bool,
    runner: Runner | None = None,
) -> list[PageResult]:
    runner = runner or run_command
    kwargs = {
        "input_pdf": input_pdf,
        "output_dir": output_dir,
        "source_sha256": source_sha256,
        "settings": settings,
        "tools": tools,
        "timeout": timeout,
        "force": force,
        "runner": runner,
    }
    outcomes: dict[int, PageResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_page, pdf_page=page, **kwargs): page for page in pages
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                outcomes[page] = future.result()
            except Exception as exc:  # Keep other explicitly selected pages running.
                outcomes[page] = PageResult(page, None, False, str(exc))
    return [outcomes[page] for page in sorted(pages)]


def process_page(
    *,
    input_pdf: Path,
    output_dir: Path,
    pdf_page: int,
    source_sha256: str,
    settings: Settings,
    tools: Tools,
    timeout: int,
    force: bool,
    runner: Runner,
) -> PageResult:
    paths = page_paths(output_dir, pdf_page)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    if not force:
        cached_record = load_valid_page_cache(
            paths, pdf_page, source_sha256, settings, tools
        )
        if cached_record is not None:
            return PageResult(pdf_page, cached_record, True)

    render_page(
        input_pdf,
        pdf_page,
        paths["image"],
        paths["render_state"],
        source_sha256,
        settings,
        tools,
        timeout,
        runner,
        force,
    )
    raw_text = checked_command(
        [
            str(tools.tesseract),
            str(paths["image"]),
            "stdout",
            "-l",
            settings.language,
            "--oem",
            str(settings.oem),
            "--psm",
            str(settings.psm),
            "--dpi",
            str(settings.dpi),
            "--tessdata-dir",
            str(tools.tessdata_dir),
        ],
        timeout,
        runner,
        f"Tesseract OCR for PDF page {pdf_page}",
    )
    normalized_text = safe_normalize_ocr_text(raw_text)
    atomic_write_text(paths["raw"], raw_text)
    atomic_write_text(paths["text"], normalized_text)

    warnings = []
    if not normalized_text:
        warnings.append("OCR produced no text")
    record = {
        "format": FORMAT_VERSION,
        "id": f"{source_sha256[:16]}:pdf-page-{pdf_page}",
        "page_index": pdf_page - 1,
        "pdf_page": pdf_page,
        "printed_page": extract_printed_page(normalized_text),
        "text": normalized_text,
        "raw_ocr_path": relative_posix(paths["raw"], output_dir),
        "normalized_text_path": relative_posix(paths["text"], output_dir),
        "image_path": relative_posix(paths["image"], output_dir),
        "settings": settings.as_dict(),
        "provenance": {
            "input_pdf": str(input_pdf),
            "input_pdf_sha256": source_sha256,
            "renderer": "pdftoppm",
            "renderer_version": tools.pdftoppm_version,
            "ocr_engine": "tesseract",
            "ocr_engine_version": tools.tesseract_version,
            "tessdata_dir": str(tools.tessdata_dir),
            "traineddata_sha256": tools.traineddata_sha256,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "semantic_ai_correction": False,
        },
        "checksums_sha256": {
            "image": file_sha256(paths["image"]),
            "raw_ocr": file_sha256(paths["raw"]),
            "normalized_text": file_sha256(paths["text"]),
        },
        "warnings": warnings,
    }
    atomic_write_json(paths["record"], record)
    return PageResult(pdf_page, record, False)


def page_paths(output_dir: Path, pdf_page: int) -> dict[str, Path]:
    stem = f"page-{pdf_page:04d}"
    return {
        "image": output_dir / "images" / f"{stem}.png",
        "raw": output_dir / "raw" / f"{stem}.txt",
        "text": output_dir / "text" / f"{stem}.txt",
        "record": output_dir / "records" / f"{stem}.json",
        "render_state": output_dir / "cache" / f"{stem}.render.json",
    }


def render_page(
    input_pdf: Path,
    pdf_page: int,
    image_path: Path,
    state_path: Path,
    source_sha256: str,
    settings: Settings,
    tools: Tools,
    timeout: int,
    runner: Runner,
    force: bool,
) -> None:
    expected_state = {
        "pdf_page": pdf_page,
        "source_sha256": source_sha256,
        "dpi": settings.dpi,
        "pdftoppm_version": tools.pdftoppm_version,
    }
    if not force and valid_render_cache(image_path, state_path, expected_state):
        return

    image_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_prefix = image_path.parent / f".{image_path.stem}.{os.getpid()}.tmp"
    temporary_image = Path(str(temporary_prefix) + ".png")
    try:
        checked_command(
            [
                str(tools.pdftoppm),
                "-f",
                str(pdf_page),
                "-l",
                str(pdf_page),
                "-singlefile",
                "-r",
                str(settings.dpi),
                "-png",
                str(input_pdf),
                str(temporary_prefix),
            ],
            timeout,
            runner,
            f"pdftoppm render for PDF page {pdf_page}",
        )
        validate_png(temporary_image)
        os.replace(temporary_image, image_path)
        state = dict(expected_state)
        state["image_sha256"] = file_sha256(image_path)
        atomic_write_json(state_path, state)
    finally:
        temporary_image.unlink(missing_ok=True)


def valid_render_cache(image_path: Path, state_path: Path, expected: dict[str, Any]) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if any(state.get(key) != value for key, value in expected.items()):
            return False
        validate_png(image_path)
        return state.get("image_sha256") == file_sha256(image_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def load_valid_page_cache(
    paths: dict[str, Path],
    pdf_page: int,
    source_sha256: str,
    settings: Settings,
    tools: Tools,
) -> dict[str, Any] | None:
    try:
        record = json.loads(paths["record"].read_text(encoding="utf-8"))
        provenance = record.get("provenance") or {}
        checksums = record.get("checksums_sha256") or {}
        if (
            record.get("format") != FORMAT_VERSION
            or record.get("pdf_page") != pdf_page
            or record.get("settings") != settings.as_dict()
            or provenance.get("input_pdf_sha256") != source_sha256
            or provenance.get("renderer_version") != tools.pdftoppm_version
            or provenance.get("ocr_engine_version") != tools.tesseract_version
            or provenance.get("traineddata_sha256") != tools.traineddata_sha256
        ):
            return None
        for name, checksum_key in (
            ("image", "image"),
            ("raw", "raw_ocr"),
            ("text", "normalized_text"),
        ):
            if checksums.get(checksum_key) != file_sha256(paths[name]):
                return None
        if record.get("text") != paths["text"].read_text(encoding="utf-8"):
            return None
        return record
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def safe_normalize_ocr_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\f", "\n")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.rstrip().replace("\ufeff", "").replace("\u200b", "") for line in value.split("\n")]
    normalized: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            normalized.append(line)
        else:
            blank_run += 1
            if blank_run <= 2:
                normalized.append("")
    return "\n".join(normalized).strip()


def extract_printed_page(text: str) -> int | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    match = NUMERIC_LINE_RE.fullmatch(lines[-1])
    if not match:
        return None
    digits = []
    try:
        for character in match.group(1):
            digits.append(str(unicodedata.digit(character)))
    except (TypeError, ValueError):
        return None
    value = int("".join(digits))
    return value if 0 < value <= 9999 else None


def write_aggregate_outputs(
    *,
    input_pdf: Path,
    output_dir: Path,
    pages: Sequence[int],
    source_sha256: str,
    settings: Settings,
    tools: Tools,
    results: Sequence[PageResult],
) -> None:
    successful = [result for result in results if result.record is not None]
    jsonl = "".join(
        json.dumps(result.record, ensure_ascii=False, sort_keys=True) + "\n"
        for result in sorted(successful, key=lambda item: item.pdf_page)
    )
    jsonl_path = output_dir / "ocr-results.jsonl"
    atomic_write_text(jsonl_path, jsonl)
    errors = [
        {"pdf_page": result.pdf_page, "error": result.error}
        for result in results
        if result.error
    ]
    manifest = {
        "format": FORMAT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "input_pdf": str(input_pdf),
        "input_pdf_sha256": source_sha256,
        "requested_pdf_pages": list(sorted(pages)),
        "successful_pages": [result.pdf_page for result in successful],
        "cached_pages": [result.pdf_page for result in results if result.cached],
        "failed_pages": errors,
        "settings": settings.as_dict(),
        "tools": {
            "pdftoppm": str(tools.pdftoppm),
            "pdftoppm_version": tools.pdftoppm_version,
            "tesseract": str(tools.tesseract),
            "tesseract_version": tools.tesseract_version,
            "tessdata_dir": str(tools.tessdata_dir),
            "traineddata_sha256": tools.traineddata_sha256,
        },
        "outputs": {
            "jsonl": jsonl_path.name,
            "jsonl_sha256": file_sha256(jsonl_path),
            "record_count": len(successful),
        },
        "semantic_ai_correction": False,
    }
    atomic_write_json(output_dir / "manifest.json", manifest)
    atomic_write_text(output_dir / "ocr-report.md", render_report(manifest))


def render_report(manifest: dict[str, Any]) -> str:
    failed = manifest["failed_pages"]
    lines = [
        "# Arabic PDF OCR report",
        "",
        f"- Input: `{manifest['input_pdf']}`",
        f"- Requested PDF pages: {', '.join(map(str, manifest['requested_pdf_pages']))}",
        f"- Successful: {len(manifest['successful_pages'])}",
        f"- From cache: {len(manifest['cached_pages'])}",
        f"- Failed: {len(failed)}",
        f"- Language: `{manifest['settings']['language']}`",
        f"- DPI: {manifest['settings']['dpi']}",
        "- Semantic AI correction: no",
    ]
    if failed:
        lines.extend(("", "## Errors", ""))
        for item in failed:
            lines.append(f"- PDF page {item['pdf_page']}: {item['error']}")
    return "\n".join(lines) + "\n"


def validate_png(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            signature = stream.read(8)
        if path.stat().st_size < 100 or signature != PNG_SIGNATURE:
            raise OcrError(f"Renderer did not create a valid PNG: {path}")
    except OSError as exc:
        raise OcrError(f"Renderer did not create PNG {path}: {exc}") from exc


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
