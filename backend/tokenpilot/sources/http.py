"""A small JSON-over-HTTPS client for the providers' admin APIs (stdlib only).

Only GET requests, so retrying is always safe: 429 and 5xx are retried with backoff,
honouring Retry-After. Numbers are parsed as Decimal (money never passes through float).
The admin key lives in the request headers only; it is never part of a URL, log or error.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from decimal import Decimal

# (url, headers) -> (status, response headers, body)
Transport = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]

TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 60


class SyncError(Exception):
    """A provider API could not be read. The message is safe to show to the user."""


def urllib_transport(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (https URLs only)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class JsonClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        headers: dict[str, str],
        key_setting: str,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.base_url = base_url
        self.headers = headers
        self.key_setting = key_setting  # named in auth errors, e.g. OPENAI_ADMIN_KEY
        self.transport = transport
        self.sleep = sleep

    def get(self, path: str, params: list[tuple[str, object]] | None = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, headers, body = self.transport(url, self.headers)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == MAX_ATTEMPTS:
                    raise SyncError(f"{self.provider}: cannot reach the API ({exc}).") from exc
                self.sleep(2**attempt)
                continue
            if status == 200:
                return json.loads(body, parse_float=Decimal)
            if status in (401, 403):
                raise SyncError(
                    f"{self.provider}: the admin key was rejected (HTTP {status}). "
                    f"Check {self.key_setting} in backend/.env. {_message(body)}".strip()
                )
            if (status == 429 or status >= 500) and attempt < MAX_ATTEMPTS:
                self.sleep(_wait_seconds(headers, attempt))
                continue
            raise SyncError(f"{self.provider}: HTTP {status} from {path}. {_message(body)}".strip())
        raise AssertionError("unreachable")

    def pages(self, path: str, params: list[tuple[str, object]]) -> Iterator[dict]:
        """Follow `next_page` cursors (the usage and cost endpoints of both providers)."""
        page = None
        while True:
            body = self.get(path, params + ([("page", page)] if page else []))
            yield body
            page = body.get("next_page")
            if not body.get("has_more") or not page:
                return


def _wait_seconds(headers: dict[str, str], attempt: int) -> float:
    retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
    try:
        wait = float(retry_after) if retry_after else 2.0**attempt
    except ValueError:
        wait = 2.0**attempt
    return min(max(wait, 0.0), MAX_WAIT_SECONDS)


def _message(body: bytes) -> str:
    """The provider's own error message, if the body has one (it never contains the key)."""
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return ""
    message = error.get("message") if isinstance(error, dict) else error
    return str(message)[:300] if message else ""
