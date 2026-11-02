from __future__ import annotations

import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import URLError

from ketab_downloader.client import FetchClient


class FakeResponse:
    status = 200

    def __init__(self, text: str) -> None:
        self.body = text.encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return "https://ketabonline.com/test"


class ClientTests(unittest.TestCase):
    def test_clone_preserves_public_settings_without_shared_delay_state(self) -> None:
        client = FetchClient(delay=2, timeout=12, max_retries=3, backoff_base=0.5, user_agent="test-agent")
        client._last_request_at = 123.0

        clone = client.clone()

        self.assertIsNot(clone, client)
        self.assertEqual(clone.delay, 2)
        self.assertEqual(clone.timeout, 12)
        self.assertEqual(clone.max_retries, 3)
        self.assertEqual(clone.backoff_base, 0.5)
        self.assertEqual(clone.user_agent, "test-agent")
        self.assertIsNone(clone._last_request_at)

    @patch("ketab_downloader.client.time.sleep", return_value=None)
    @patch("ketab_downloader.client.urlopen")
    def test_retries_temporary_network_error(self, mocked_open, mocked_sleep) -> None:
        mocked_open.side_effect = [URLError("temporary"), FakeResponse("<html>ok</html>")]
        client = FetchClient(delay=0, max_retries=2, backoff_base=0.1)
        result = client.fetch("https://ketabonline.com/test")
        self.assertEqual(result.status, 200)
        self.assertEqual(mocked_open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
