import time
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: str
    final_url: str
    retry_after_seconds: float | None = None


class HttpClient:
    def __init__(
        self,
        timeout_seconds: float,
        rate_limit_seconds: float,
        retries: int,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 60,
        same_host_redirects: bool = False,
    ) -> None:
        if timeout_seconds <= 0 or rate_limit_seconds < 0 or not 0 <= retries <= 10:
            raise ValueError("HTTP timeout must be positive; rate nonnegative; retries 0–10")
        if backoff_seconds < 0 or max_backoff_seconds < backoff_seconds:
            raise ValueError("Invalid HTTP backoff bounds")
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.same_host_redirects = same_host_redirects
        self.metrics = dict(requests=0, successful_requests=0, http_errors=0, retries=0, transport_errors=0)
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
            self.metrics["requests"] += 1
            self.metrics["retries"] += int(attempt > 0)
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
                open_request = urllib.request.urlopen
                if self.same_host_redirects:
                    original_host = urlsplit(url).hostname

                    class PublicRedirect(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self, req, fp, code, msg, headers, newurl):
                            destination = urlsplit(newurl)
                            aliases = {original_host}
                            if original_host and original_host.endswith("linkedin.com"):
                                aliases.update({"www.linkedin.com", "at.linkedin.com"})
                            if destination.scheme != "https" or destination.hostname not in aliases:
                                raise urllib.error.HTTPError(req.full_url, 403, "External redirect refused", headers, fp)
                            return super().redirect_request(req, fp, code, msg, headers, newurl)

                    open_request = urllib.request.build_opener(PublicRedirect()).open
                with open_request(request, timeout=self.timeout_seconds) as response:
                    self.metrics["successful_requests"] += 1
                    charset = response.headers.get_content_charset() or "utf-8"
                    body = response.read().decode(charset, errors="replace")
                    return HttpResponse(
                        status_code=response.status,
                        body=body,
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as error:
                self.metrics["http_errors"] += 1
                charset = error.headers.get_content_charset() or "utf-8"
                body = error.read().decode(charset, errors="replace")
                retry_after = error.headers.get("Retry-After", "") if hasattr(error.headers, "get") else ""
                try:
                    delay = float(retry_after)
                except ValueError:
                    from datetime import datetime, timezone
                    from email.utils import parsedate_to_datetime
                    try:
                        delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        delay = 0
                delay = delay if math.isfinite(delay) and delay > 0 else 0
                response = HttpResponse(error.code, body, error.geturl(), delay or None)
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    return response
                last_error = error
                if delay > self.max_backoff_seconds:
                    # Do not retry earlier than requested, or block the bounded run for minutes.
                    return response
                time.sleep(max(0, delay, min(self.max_backoff_seconds, self.backoff_seconds * 2**attempt)))
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                self.metrics["transport_errors"] += 1
                last_error = error
                if attempt == self.retries:
                    break
            time.sleep(min(self.max_backoff_seconds, self.backoff_seconds * (2**attempt)))

        raise RuntimeError(str(last_error) if last_error else "HTTP request failed")
