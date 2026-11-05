from job_radar.http import HttpResponse
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class PlaywrightClient:
    """One isolated browser per bounded probe; no saved account session."""

    def __init__(self, timeout_seconds: float = 20, headed: bool = False) -> None:
        self.timeout_ms = timeout_seconds * 1000
        self.headed = headed
        self._last_action = 0.0

    def _wait(self) -> None:
        time.sleep(max(0, 2 - (time.monotonic() - self._last_action)))
        self._last_action = time.monotonic()

    def get(self, url: str) -> HttpResponse:
        self._wait()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                'Install scraping extras: pip install -e ".[scraping]"; '
                "then python -m playwright install chromium"
            ) from None

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed, timeout=self.timeout_ms)
            try:
                context = browser.new_context(locale="de-AT")
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if response is None:
                    raise RuntimeError("Navigation returned no HTTP response")
                return HttpResponse(response.status, page.content(), page.url)
            finally:
                browser.close()

    def iter_search_pages(self, url: str, source: str, max_pages: int):
        """Use the site's load-more control so every original filter is retained."""
        from playwright.sync_api import sync_playwright

        selectors = {
            "karriere": (".m-jobsListItem", ".m-loadMoreJobsButton__button"),
            "linkedin": (".base-search-card", ".infinite-scroller__show-more-button"),
        }
        cards, button = selectors[source]
        self._wait()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed, timeout=self.timeout_ms)
            try:
                page = browser.new_page(locale="de-AT")
                page.set_default_timeout(self.timeout_ms)
                response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if response is None:
                    raise RuntimeError("No navigation response")
                for index in range(max_pages):
                    yield HttpResponse(response.status, page.content(), page.url)
                    if index + 1 == max_pages or response.status != 200:
                        return
                    previous = page.locator(cards).count()
                    if source == "linkedin":
                        # LinkedIn's public frontend loads additional HTML cards
                        # from this route; it is not an official search API.
                        parsed = urlsplit(url)
                        pairs = [
                            (k, v)
                            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                            if k != "start"
                        ]
                        if index == 0:
                            offset = int(dict(parse_qsl(parsed.query)).get("start", "0"))
                        offset += previous
                        pairs.append(("start", str(offset)))
                        next_url = urlunsplit(
                            (
                                "https",
                                parsed.netloc,
                                "/jobs-guest/jobs/api/seeMoreJobPostings/search",
                                urlencode(pairs),
                                "",
                            )
                        )
                        self._wait()
                        response = page.goto(
                            next_url, wait_until="domcontentloaded", timeout=self.timeout_ms
                        )
                        if response is None:
                            raise RuntimeError("No pagination response")
                        continue
                    control = page.locator(button).first
                    if not control.count():
                        return
                    self._wait()
                    # Scrolling may itself append a batch before the button is visible.
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    if page.locator(cards).count() <= previous and control.is_visible():
                        control.click(timeout=self.timeout_ms)
                    page.wait_for_function(
                        "([selector, count]) => document.querySelectorAll(selector).length > count",
                        arg=[cards, previous],
                        timeout=self.timeout_ms,
                    )
            finally:
                browser.close()
