# Social Media Analytics Tool

[![Run Python Tests](https://github.com/DuhShmere/Social-Media-Analytics-Tool/actions/workflows/tests.yml/badge.svg)](https://github.com/DuhShmere/Social-Media-Analytics-Tool/actions/workflows/tests.yml)

A Flask web application that tracks social-media post performance over time. It imports posts from the YouTube Data API, the Instagram API, the TikTok API, CSV files, or manual entry, records every measurement as a historical snapshot, and turns them into engagement metrics, growth trends, and a recommended posting window.

![Analytics dashboard](docs/screenshots/dashboard.png)

## Features

- **YouTube sync:** import a channel by handle or link, then re-sync to update metrics. Each sync records a new snapshot, so growth can be measured.
- **Instagram sync:** connect an Instagram professional account through Instagram's own login (OAuth), then sync views, likes, comments, shares, and saves. Access tokens are encrypted and renewed automatically.
- **TikTok sync:** connect a TikTok account through TikTok's login (OAuth), then sync views, likes, comments, and shares for recent public videos. The 24-hour access token is refreshed automatically, and disconnecting revokes access on TikTok's side.
- **CSV import and export:** row-by-row validation with clear errors, duplicate detection, a downloadable sample file, and formula-safe exports.
- **Manual posts:** add, edit, and delete posts that did not come from YouTube.
- **Analytics dashboard:** filter by account, platform, content type, date range, and text; see totals, engagement rate, top posts, engagement over time, and interaction breakdown.
- **Posting-time recommendation:** the weekday and 3-hour window with the highest engagement, using at least 3 posts.
- **Post history:** per-post growth since the previous snapshot and charts of views, interactions, and engagement over time.
- **Accessible, responsive interface:** keyboard focus styles, skip link, labeled forms, announced messages, and layouts that work on phones.

| Post history | Phone layout |
|---|---|
| ![Post detail page with growth and history charts](docs/screenshots/post-detail.png) | ![Dashboard on a phone](docs/screenshots/mobile-dashboard.png) |

## Tech stack

**Backend:** Python 3.12, Flask, Flask-SQLAlchemy, Flask-Migrate (Alembic), Flask-WTF, SQLite
**Frontend:** Jinja templates, HTML, CSS, vanilla JavaScript, Chart.js
**Integrations:** YouTube Data API v3 (google-api-python-client), Instagram API with Instagram Login and TikTok Login Kit and Display API (OAuth 2.0, requests), Fernet token encryption (cryptography)
**Quality and delivery:** pytest (294 tests), GitHub Actions, Docker, gunicorn

## Architecture

```
Browser ──> routes/ (Flask blueprints) ──> services/ ──> models.py ──> SQLite
                                               ├──> YouTube Data API
                                               ├──> Instagram API (OAuth)
                                               └──> TikTok API (OAuth)
```

- **`routes/`** holds thin request handlers, one blueprint per feature area.
- **`services/`** holds the business logic for analytics, CSV, and YouTube. It never reads the Flask request, so it is unit-testable without HTTP.
- **Transactions:** services add changes to the database session; the route decides whether to commit or roll back.
- **App factory:** `create_app()` builds the app from a per-environment configuration chosen by `APP_ENV`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams, the data flows, design decisions, and known limitations.

### Database model

| Table | Purpose |
|---|---|
| `social_account` | A connected channel or account. Unique on `(platform, external_account_id)`. Instagram and TikTok accounts also store encrypted OAuth tokens and their expiry; TikTok adds a refresh token. |
| `post` | A post's current metrics and its `source` (`manual`, `csv_import`, `youtube_api`, `instagram_api`, or `tiktok_api`). Unique on `(platform, external_id)`, which makes re-syncs and re-imports update instead of duplicate. |
| `metric_snapshot` | One row per measurement, deleted with its post. Growth and trend charts compare snapshots. |

Schema changes go through Flask-Migrate. Every unique and foreign-key constraint is named, which SQLite migrations require.

## Getting started

Requires Python 3.12. Commands are for macOS and Linux.

```bash
git clone https://github.com/DuhShmere/Social-Media-Analytics-Tool.git
cd Social-Media-Analytics-Tool

python3.12 -m venv venv
./venv/bin/python -m pip install -r requirements.txt

cp .env.example .env              # then add your YouTube API key
./venv/bin/python -m flask --app app db upgrade
./venv/bin/python app.py
```

Open http://127.0.0.1:5001. The database is created at `instance/tracker.db`.

### Database migrations

```bash
# Apply all migrations (run after pulling changes)
./venv/bin/python -m flask --app app db upgrade

# After changing models.py, generate a migration, review it, then apply it
./venv/bin/python -m flask --app app db migrate -m "Describe the change"
```

### YouTube API setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Go to **APIs & Services → Library** and enable **YouTube Data API v3**.
3. Go to **APIs & Services → Credentials → Create credentials → API key**.
4. Restrict the key to **YouTube Data API v3**.
5. Put it in `.env` as `YOUTUBE_API_KEY=...`. Never commit `.env`; it is already in `.gitignore`.

Each sync uses 3 quota units: one call each for the channel, its uploads, and the video statistics. The default quota is 10,000 units per day.

### Instagram setup (optional)

Instagram sync uses the **Instagram API with Instagram Login**. It works with Instagram **Business or Creator** accounts; a personal account can switch to Creator for free in Instagram's settings.

1. In [Meta for Developers](https://developers.facebook.com/), create an app of type **Business** and add the **Instagram** product, choosing **API setup with Instagram login**.
2. Under **App roles → Roles**, scroll to the **Instagram Testers** section at the bottom and add your Instagram username. (The plain **Testers** role near the top is a Facebook role and sends no Instagram invite.) Then, in a desktop browser logged in to that Instagram account, open https://www.instagram.com/accounts/manage_access/, choose **Tester Invites**, and accept. While the Meta app is in development mode, only tester accounts can connect.
3. Meta requires an **HTTPS** redirect address and does not accept `localhost`. For local testing, run an HTTPS tunnel to the app, for example:
   ```bash
   ngrok http 5001
   ```
4. On **Instagram → API setup with Instagram business login**, open section **3. Set up Instagram business login** and save the redirect URL `https://<your-tunnel-address>/instagram/callback`. Without it, Instagram's login page shows **"Invalid redirect_uri"**. If Meta saves it with a trailing slash, add the same slash to `INSTAGRAM_REDIRECT_URI`; the app accepts both.
5. Generate an encryption key for stored tokens:
   ```bash
   ./venv/bin/python -c "from token_vault import generate_key; print(generate_key())"
   ```
   Then add these to `.env`, pasting the printed key, and restart the app:
   ```bash
   INSTAGRAM_APP_ID=your-instagram-app-id
   INSTAGRAM_APP_SECRET=your-instagram-app-secret
   INSTAGRAM_REDIRECT_URI=https://<your-tunnel-address>/instagram/callback
   TOKEN_ENCRYPTION_KEY=the-key-printed-above
   ```
   Keep the key stable: changing it means reconnecting every account.
6. Open the app **through the tunnel address**, not `127.0.0.1`, and choose **Accounts → Connect Instagram**. The login check is tied to the address you started from.

What gets synced: the 50 most recent posts, with likes and comments from each post and views, shares, and saves from Instagram's insights. Instagram documents post captions as unavailable with Instagram Login, so posts are titled by type and date, such as "Instagram Reel · Oct 12, 2026". Instagram image links expire, so thumbnails refresh on each sync.

### TikTok setup (optional)

TikTok sync uses **Login Kit** and the **Display API**. While the TikTok app is in **sandbox** mode, it can connect up to 10 TikTok accounts you add as target users, without app review.

1. Sign in at [TikTok for Developers](https://developers.tiktok.com/), open **Manage apps**, and create an app.
2. Switch the app to **Sandbox** and click **Create Sandbox**.
3. Add the **Login Kit** product for the **Web** platform, and add the scopes **user.info.basic**, **user.info.profile**, and **video.list**.
4. In Login Kit's settings, add the redirect URI `https://<your-tunnel-address>/tiktok/callback`. TikTok requires `https` and does not allow query parameters, so use the same HTTPS tunnel as for Instagram.
5. In **Sandbox settings → Target users**, click **Add account** and log in with your TikTok account.
6. Copy the sandbox's **Client key** and **Client secret** into `.env`, then restart the app:
   ```bash
   TIKTOK_CLIENT_KEY=your-client-key
   TIKTOK_CLIENT_SECRET=your-client-secret
   TIKTOK_REDIRECT_URI=https://<your-tunnel-address>/tiktok/callback
   ```
   `TOKEN_ENCRYPTION_KEY` (from the Instagram steps) is shared.
7. Open the app through the tunnel address and choose **Accounts → Connect TikTok**.

What gets synced: the 50 most recent **public** videos, with views, likes, comments, and shares. TikTok's API does not provide saves. Posts are titled with the video's title or description.

### CSV format

Required columns: `platform`, `caption`, `content_type`, `posted_at`, `views`, `likes`, `comments`, `shares`, `saves`.
Optional columns: `external_id`, `external_url`.

Dates use ISO format, such as `2026-09-15 14:30` or `2026-09-15T14:30:00-07:00`; offsets are converted to UTC. Download a working example from **Import CSV → Download sample CSV**.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `APP_ENV` | No | `development` (default), `production`, or `testing` |
| `YOUTUBE_API_KEY` | For YouTube features | YouTube Data API v3 key |
| `DATABASE_URL` | In production | SQLAlchemy URL; defaults to `instance/tracker.db` in development |
| `SECRET_KEY` | In production | Signs session cookies and CSRF tokens; generated per run in development |
| `SESSION_COOKIE_SECURE` | No | Production only; set to `false` when serving over plain HTTP |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | For Instagram | From the Meta app's Instagram settings |
| `INSTAGRAM_REDIRECT_URI` | For Instagram | Must exactly match a redirect URI saved in the Meta app |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | For TikTok | From the TikTok app's sandbox or production settings |
| `TIKTOK_REDIRECT_URI` | For TikTok | Must start with `https` and match a redirect URI saved in Login Kit |
| `TOKEN_ENCRYPTION_KEY` | For Instagram or TikTok | Encrypts stored OAuth tokens; generate with `token_vault.generate_key()` |

Production refuses to start without `DATABASE_URL` and `SECRET_KEY`.

## Testing

```bash
./venv/bin/python -m pytest -v
```

The 294 tests use an in-memory database and never touch `tracker.db` or call the real YouTube, Instagram, or TikTok APIs. They cover:

- Analytics calculations and filters
- CSV parsing edge cases
- YouTube sync, using mocked responses that exercise the client library's real retry logic
- The Instagram and TikTok OAuth flows, token encryption, refresh, and revocation, and sync, using responses shaped like each platform's documented examples
- Validation and error pages
- CSRF and security headers
- Accessibility checks, including color contrast read from the stylesheet and form labels
- Logging and configuration

GitHub Actions runs the suite on every push, then builds the Docker image, starts it, and checks that it reports healthy.

## Running with Docker

```bash
docker build -t social-analytics .
docker run -p 8000:8000 \
  -e SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  -e DATABASE_URL=sqlite:////app/instance/tracker.db \
  -e SESSION_COOKIE_SECURE=false \
  -e YOUTUBE_API_KEY=your-key \
  -v analytics-data:/app/instance \
  social-analytics
```

Open http://127.0.0.1:8000. The container:

- applies migrations on startup
- serves the app with gunicorn as a non-root user
- logs JSON lines
- exposes `GET /health` for its health check

## Security and reliability

- CSRF tokens on every form, and `HttpOnly`, `SameSite=Lax` session cookies
- A strict Content Security Policy (no inline scripts or styles), anti-framing headers, and Chart.js pinned with a Subresource Integrity hash
- Validation on every input; imported links are rendered only if they use `http` or `https`
- Instagram and TikTok logins are verified with a single-use `state` value, OAuth tokens are encrypted at rest, and disconnecting TikTok revokes its access
- API keys, access tokens, app secrets, and login codes are redacted from all logs, including tracebacks
- YouTube, Instagram, and TikTok requests use timeouts and retry only temporary failures, and no data is written until every API call succeeds
- Structured logs with a request ID on every line, plus a `/health` endpoint that checks the database

## Project structure

```
app.py, app_factory.py, config.py   Entry point, app factory, settings
routes/                             Blueprints: main, posts, imports, instagram, tiktok, api, health
services/                           analytics.py, csv_service.py, youtube.py, instagram.py, tiktok.py, http.py
token_vault.py, redaction.py        Token encryption, secret redaction in logs
models.py, validators.py            Database models and input validation
errors.py, security.py,             Error pages, security headers,
logging_config.py                   structured logging
migrations/                         Alembic migrations
templates/, static/                 Jinja templates, CSS, JavaScript
tests/                              pytest suite
docs/                               Architecture notes and screenshots
```

## Roadmap

- Run YouTube syncs in a background job instead of during the web request
- User accounts and authentication
- TikTok app review, so accounts beyond sandbox target users can connect
- Pagination for very large post libraries
