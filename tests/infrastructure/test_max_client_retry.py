import httpx

from app.infrastructure.services.max_client import _is_retryable_max_error


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/messages")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_retryable_on_timeout_and_transport_error():
    request = httpx.Request("GET", "https://example.com")
    timeout_exc = httpx.ReadTimeout("timeout", request=request)
    transport_exc = httpx.ConnectError("connect", request=request)
    assert _is_retryable_max_error(timeout_exc)
    assert _is_retryable_max_error(transport_exc)


def test_retryable_on_429_and_5xx_only():
    assert _is_retryable_max_error(_http_status_error(429))
    assert _is_retryable_max_error(_http_status_error(500))
    assert not _is_retryable_max_error(_http_status_error(400))
