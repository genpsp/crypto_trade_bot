from __future__ import annotations

import time
from typing import Callable

import requests

RETRIABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


def retry_delay_seconds(base_delay_seconds: float, attempt: int) -> float:
    return base_delay_seconds * (2 ** (attempt - 1))


def request_with_retry(
    request_fn: Callable[[], requests.Response],
    *,
    attempts: int,
    base_delay_seconds: float,
    context: str,
    retriable_status_codes: frozenset[int] = RETRIABLE_HTTP_STATUS_CODES,
) -> requests.Response:
    response: requests.Response | None = None
    last_error_message = f"{context}: retry attempts exhausted"
    total_attempts = max(attempts, 1)

    for attempt in range(1, total_attempts + 1):
        try:
            response = request_fn()
        except requests.RequestException as error:
            last_error_message = f"{context}: {error}"
            if attempt < total_attempts:
                time.sleep(retry_delay_seconds(base_delay_seconds, attempt))
                continue
            raise RuntimeError(last_error_message) from error

        if response.status_code == 200:
            return response

        last_error_message = f"{context}: HTTP {response.status_code}"
        should_retry = response.status_code in retriable_status_codes and attempt < total_attempts
        if should_retry:
            time.sleep(retry_delay_seconds(base_delay_seconds, attempt))
            continue
        raise RuntimeError(last_error_message)

    raise RuntimeError(last_error_message)
