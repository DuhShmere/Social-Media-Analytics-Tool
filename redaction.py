"""Keep secrets out of log output.

API clients put credentials in request URLs (YouTube's key=, Instagram's
access_token= and client_secret=, OAuth's one-time code=), and those URLs
appear in retry warnings, exception messages, and tracebacks.
"""

import logging
import re


SECRET_PARAMETER_PATTERN = re.compile(
    r"([?&](?:key|access_token|client_secret|code)=)[^&\s'\"]+"
)


def redact_secrets(text):
    return SECRET_PARAMETER_PATTERN.sub(r"\1REDACTED", text)


class SecretRedactionFilter(logging.Filter):
    """Remove secret URL parameters from log messages and tracebacks."""

    def filter(self, record):
        record.msg = redact_secrets(record.getMessage())
        record.args = None

        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(
                record.exc_info
            )

        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)

        return True


# Logger filters only apply to records created by that exact logger,
# so every library logger that can log a request URL gets the filter.
for logger_name in (
    "googleapiclient.discovery",
    "googleapiclient.http",
    "googleapiclient.model",
    "urllib3.connectionpool",
):
    logging.getLogger(logger_name).addFilter(SecretRedactionFilter())
