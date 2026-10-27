"""JSON over HTTP with a timeout and retries for temporary failures.

Shared by the Instagram and TikTok services. Each caller decides whether
a request is safe to retry: reads are, but exchanging a one-time login
code is not, because the code can only be used once.
"""

import time

import requests


REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def parse_json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def request_json(
    session,
    method,
    url,
    *,
    describe_error,
    network_error,
    retry,
    sleep=time.sleep,
    **kwargs,
):
    """Send a request and return the JSON body.

    describe_error(status_code, body) and network_error() return the
    exception to raise; retried waits are 1 s, then 2 s.
    """
    attempts = MAX_ATTEMPTS if retry else 1

    for attempt in range(1, attempts + 1):
        try:
            response = session.request(
                method,
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt < attempts:
                sleep(2 ** (attempt - 1))
                continue

            raise network_error() from None

        if response.status_code in RETRY_STATUS_CODES and attempt < attempts:
            sleep(2 ** (attempt - 1))
            continue

        body = parse_json(response)

        if not response.ok:
            raise describe_error(response.status_code, body)

        return body

    raise network_error()
