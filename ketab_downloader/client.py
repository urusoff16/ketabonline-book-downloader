from __future__ import annotations

import logging
import random
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    text: str
    content_type: str


class PermanentFetchError(RuntimeError):
    pass


class FetchClient:
    def __init__(
        self,
        *,
        delay: float = 1.5,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        user_agent: str = "KetabOnlineStructuredArchiver/0.1 (+personal archival; limited concurrent requests)",
        logger: logging.Logger | None = None,
    ) -> None:
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.1, backoff_base)
        self.user_agent = user_agent
        self.logger = logger or logging.getLogger(__name__)
        self._last_request_at: float | None = None

    def clone(self) -> "FetchClient":
        return FetchClient(
            delay=self.delay,
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            user_agent=self.user_agent,
            logger=self.logger,
        )

    def fetch(self, url: str) -> FetchResult:
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_between_requests()
            request = Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                    "Accept-Language": "ar,en;q=0.8",
                    "Connection": "close",
                },
            )
            try:
                self.logger.info("GET %s (attempt %s/%s)", url, attempt + 1, self.max_retries + 1)
                with urlopen(request, timeout=self.timeout) as response:
                    self._last_request_at = time.monotonic()
                    body = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                    text = body.decode(charset, errors="strict")
                    return FetchResult(
                        url=response.geturl(),
                        status=response.status,
                        text=text,
                        content_type=response.headers.get_content_type(),
                    )
            except HTTPError as exc:
                self._last_request_at = time.monotonic()
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in TRANSIENT_STATUS:
                    raise PermanentFetchError(f"HTTP {exc.code} for {url}\n{body[:500]}") from exc
                last_error = exc
                self.logger.warning("Temporary HTTP %s for %s", exc.code, url)
            except (URLError, TimeoutError, socket.timeout, UnicodeDecodeError) as exc:
                self._last_request_at = time.monotonic()
                last_error = exc
                self.logger.warning("Temporary fetch failure for %s: %s", url, exc)

            if attempt < self.max_retries:
                wait = self.backoff_base * (2**attempt) + random.uniform(0.0, 0.25)
                self.logger.info("Retrying in %.2f seconds", wait)
                time.sleep(wait)

        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries + 1} attempts") from last_error

    def _wait_between_requests(self) -> None:
        if self._last_request_at is None or self.delay <= 0:
            return
        remaining = self.delay - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)


def save_problem_response(directory: Path, part: int, page: int, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    path = directory / f"part-{part}-page-{page}-{stamp}.html"
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
