import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: str
    final_url: str


class HttpClient:
    def __init__(
        self,
        timeout_seconds: float,
        rate_limit_seconds: float,
        retries: int,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.rate_limit_seconds = rate_limit_seconds
        self.retries = retries
        self._last_request: dict[str, float] = {}

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request[host] = time.monotonic()

    def get(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait(url)
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/136.0.0.0 Safari/537.36"
                    ),
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": "de-AT,de;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    body = response.read().decode(charset, errors="replace")
                    return HttpResponse(
                        status_code=response.status,
                        body=body,
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as error:
                charset = error.headers.get_content_charset() or "utf-8"
                body = error.read().decode(charset, errors="replace")
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    return HttpResponse(error.code, body, error.geturl())
                last_error = error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt == self.retries:
                    break
            time.sleep(0.5 * (2**attempt))

        raise RuntimeError(str(last_error) if last_error else "HTTP request failed")
