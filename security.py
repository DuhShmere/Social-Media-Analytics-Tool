"""Security headers added to every response.

They are applied in every environment, so a page that would break
under the Content Security Policy fails in development, not after
deployment.
"""

from flask import request


# Only this app's own files, plus the pinned Chart.js and YouTube
# thumbnails. No inline scripts or styles are allowed, which blocks
# most cross-site scripting even if escaping were ever missed.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self'",
        # YouTube thumbnails and Instagram's and TikTok's image CDNs.
        "img-src 'self' https://i.ytimg.com https://*.cdninstagram.com "
        "https://*.fbcdn.net https://*.tiktokcdn.com "
        "https://*.tiktokcdn-us.com data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def add_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)

    # Browsers ignore HSTS over plain HTTP, so only send it over HTTPS.
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    return response


def register_security_headers(app):
    app.after_request(add_security_headers)
