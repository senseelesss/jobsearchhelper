import urllib.error

from job_radar.http import HttpClient


class _Headers:
    def get_content_charset(self) -> str:
        return "utf-8"


class _Response:
    status = 200
    headers = _Headers()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"<html>ok</html>"

    def geturl(self) -> str:
        return "https://example.com/final"


def test_http_client_retries_transport_failure_with_backoff(monkeypatch) -> None:
    calls: list[float] = []
    sleeps: list[float] = []

    def urlopen(request: object, timeout: float) -> _Response:
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary failure")
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)

    response = HttpClient(
        timeout_seconds=7.5,
        rate_limit_seconds=0,
        retries=2,
    ).get("https://example.com/job")

    assert calls == [7.5, 7.5]
    assert sleeps == [0.5]
    assert response.status_code == 200
    assert response.final_url == "https://example.com/final"
