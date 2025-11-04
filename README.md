# Music Catalog Backend (Skeleton)

FastAPI + SQLAlchemy + httpx + PostgreSQL.  
DB-first search; external Spotify fetch only on user-confirmed sync.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill values
uvicorn app.main:app --reload
```

## Endpoints
- GET /api/search?mode=artist|album&q=...&limit=&offset=...  # DB-only
- GET /api/search/external?mode=album&q=...&artist=...       # Spotify candidates (no DB write)
- GET /api/albums/{album_id}                                  # Album detail from DB
- POST /api/albums/sync { "spotify_album_id": "...", "market": "KR" }  # Confirmed sync
- GET /api/artists/{artist_id}/albums                         # Albums by artist (DB)

## Notes
- SingleFlight prevents duplicate sync on same album.
- Implement actual joins in AlbumService/ArtistService as needed.
- Map release_date precision via utils.mapping.
