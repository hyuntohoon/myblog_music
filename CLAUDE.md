# myblog_music

FastAPI music metadata service deployed as AWS Lambda via Mangum. Provides album/artist/track search and queues album sync jobs to SQS.

## Stack

- **Runtime**: Python 3.12, FastAPI, Mangum (Lambda adapter)
- **DB**: PostgreSQL via SQLAlchemy 2 + psycopg3; ORM models from `myblog-shared-db` package (`myblog_shared_db.models`)
- **External**: Spotify Web API (via `app/clients/spotify_client.py`)
- **Queue**: AWS SQS producer (via `app/clients/sqs_client.py`)
- **Deploy**: `build.sh` → zip

## Structure

```
app/
├── main.py              ← FastAPI app, router registration
├── core/config.py       ← Settings (pydantic BaseSettings)
├── api/routers/         ← albums.py, artists.py, search.py
├── clients/
│   ├── spotify_client.py ← Spotify API wrapper
│   └── sqs_client.py     ← SQS producer (enqueue_album_sync)
├── domain/schemas.py    ← Pydantic response schemas (local)
├── repositories/        ← DB access layer
├── services/            ← Business logic
├── mappers/             ← Domain ↔ API response mapping
└── utils/
```

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/music/albums/{id}` | Get album with tracks |
| GET | `/api/music/artists/{id}` | Get artist detail |
| GET | `/api/music/search/unified` | DB-only unified search |
| POST | `/api/music/candidates` | Enqueue new albums to SQS |

## SQS Message Contract

Defined in `docs/contracts/sqs-album-sync.md` (workspace repo).

`SqsClient.enqueue_album_sync(album_ids, market)` sends Format A messages:
```json
{"album_ids": ["<spotify_id>", ...], "market": "KR"}
```
Up to 20 IDs per message, sent via `send_message_batch`.

## Hard Rules

- **Never add a synchronous Spotify API call to `/search/unified`** — that endpoint must remain DB-only. Spotify calls go through the async path only: `candidates` → SQS → Worker.
- **Never call `print()`** — use `logging.getLogger(__name__)`.
- **Never work directly on `main`** — branch from `main`, PR back.

## Config

```
DATABASE_URL=postgresql+psycopg://...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
ENV=local|dev|prod
AWS_DEFAULT_REGION=ap-northeast-2
QUEUE_NAME=test-queue
LOCALSTACK_ENDPOINT=http://localhost:4566   # local only
AWS_ACCOUNT_ID=000000000000                # local only
SQS_QUEUE_URL=...                          # overrides queue_name lookup if set
```

## Running Locally

```bash
pip install -r requirements.txt
ENV=local uvicorn app.main:app --reload --port 8001
```

For local SQS testing, start LocalStack and set `LOCALSTACK_ENDPOINT=http://localhost:4566`.

## Tests

```bash
pytest
```

Test config in `pytest.ini`. Integration tests require a running PostgreSQL instance.

## Verification

```bash
python -c "from app.main import app; print('import ok')"
pytest --tb=short
```
