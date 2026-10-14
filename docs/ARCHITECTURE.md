# Architecture

Social Media Analytics Tool is a server-rendered Flask application. It collects post metrics from three sources (manual entry, CSV files, and the YouTube Data API), stores every measurement as a snapshot, and reports engagement, growth, and posting-time recommendations.

## Request flow

```mermaid
flowchart LR
    Browser -->|HTML pages, forms| Routes
    Browser -->|fetch JSON| API[routes/api.py]
    Routes[routes/ blueprints] --> Services
    API --> Services
    Services[services/ analytics, csv, youtube] --> Models[models.py]
    Services -->|HTTPS, retries| YouTube[(YouTube Data API)]
    Models --> DB[(SQLite)]
```

Pages are rendered with Jinja. The dashboard and post pages then call two JSON endpoints to draw Chart.js charts. Routes stay thin: they read the request, call a service, and commit or roll back.

## Modules

| Layer | Files | Responsibility |
|---|---|---|
| Entry and setup | `app.py`, `app_factory.py`, `config.py` | `create_app()` builds the app from a per-environment config chosen by `APP_ENV` |
| Routes | `routes/main.py`, `posts.py`, `imports.py`, `api.py`, `health.py` | One blueprint per feature area; 13 URL rules |
| Services | `services/analytics.py`, `csv_service.py`, `youtube.py` | Business logic with no dependency on the Flask request |
| Data | `models.py`, `extensions.py`, `migrations/` | SQLAlchemy models and Alembic migrations |
| Input checks | `validators.py` | Shared validation with user-safe error messages |
| Cross-cutting | `errors.py`, `security.py`, `logging_config.py` | Error pages, security headers, structured logs |
| Front end | `templates/`, `static/js/`, `static/css/` | Jinja templates, vanilla JavaScript, one stylesheet |

## Data model

```mermaid
erDiagram
    SOCIAL_ACCOUNT ||--o{ POST : "has"
    POST ||--o{ METRIC_SNAPSHOT : "records"
    SOCIAL_ACCOUNT {
        int id
        string platform
        string external_account_id "unique with platform"
        datetime last_synced_at
    }
    POST {
        int id
        string platform
        string external_id "unique with platform"
        string source "manual, csv_import, youtube_api"
        int views
        int likes
    }
    METRIC_SNAPSHOT {
        int id
        int post_id "deleted with its post"
        datetime captured_at
        int views
        int likes
    }
```

- A post's current metrics live on `post`. Every create, edit, import, and sync also appends a `metric_snapshot`, so growth and trend charts compare snapshots rather than overwriting history.
- `(platform, external_id)` is unique, which is how re-syncs and re-imports update existing posts instead of duplicating them.
- Only `manual` and `csv_import` posts can be edited or deleted; synced posts are owned by YouTube.
- Every unique and foreign-key constraint is named, which SQLite batch migrations require.

## Key flows

**YouTube sync** (`services/youtube.py`). `YouTubeClient` fetches the channel, its 50 most recent uploads, and their statistics. Only then does `sync_channel()` write the account, posts, and one snapshot per video, so a failed API call never leaves partial data. Requests use a 15-second timeout and the client library's retries, which cover only transient failures (5xx, 429, rate limits, dropped connections). Quota and invalid-key errors fail immediately with a specific message.

**CSV import** (`services/csv_service.py`). Each row is validated on its own; invalid rows are reported by row number while valid rows still import. Duplicates are skipped by `external_id` or, for rows without one, by matching platform, caption, content type, and time. Exports escape cells that start with `=`, `+`, `-`, or `@` so spreadsheets do not run them as formulas.

**Analytics** (`services/analytics.py`). Filters are parsed into a `PostFilters` dataclass and applied to a query, so the same code serves the JSON API and CSV export. The dashboard's post table filters in the browser for instant feedback, while the charts and summary come from the API.

## Design decisions

- **Services do not read the Flask request.** They take plain values, which keeps them unit-testable without HTTP.
- **The caller owns the transaction.** Services add to the session; routes commit or roll back, so one place decides what is saved.
- **Fail at startup, not at runtime.** Production refuses to start without `DATABASE_URL` and `SECRET_KEY`.
- **Server-rendered pages with small scripts.** No front-end framework; JavaScript only draws charts, filters the table, and manages form loading states.

## Security

- CSRF tokens on every form (Flask-WTF), with `SameSite=Lax` session cookies.
- A strict Content Security Policy with no inline scripts or styles, plus anti-framing and `nosniff` headers. Chart.js is pinned with a Subresource Integrity hash.
- Only `http(s)` links from imported data are rendered as links.
- The YouTube API key is redacted from all log output, including tracebacks.

## Operations

- **Health:** `GET /health` checks the database and returns 200 or 503.
- **Logs:** JSON lines in production, with a request ID on every line and per-request timing.
- **Container:** the Dockerfile runs migrations, then gunicorn, as a non-root user with a health check.
- **CI:** GitHub Actions runs 183 tests, then builds the image and checks the running container.

## Known limitations

- Sync runs inside the web request, so a large channel makes the user wait; a background job queue would remove that.
- Only the 50 most recent videos per channel are synced.
- There are no user accounts; the app assumes a single trusted user.
- The dashboard renders every post, which suits hundreds of posts but would need pagination for many thousands.
- Only SQLite is tested. `DATABASE_URL` accepts other SQLAlchemy databases, but PostgreSQL would need a driver such as `psycopg` and has not been verified.
