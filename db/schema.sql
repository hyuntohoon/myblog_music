-- DERIVED from docs/contracts/schema.sql (myblog-workspace repo).
-- Do not edit here first — update the canonical file, then sync this copy.
-- Last synced: 2026-05-23

-- ========== EXTENSIONS ==========
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ========== ENUMS ==========
CREATE TYPE post_status AS ENUM ('draft', 'published', 'archived');

-- ========== CATEGORIES & TAGS ==========
CREATE TABLE categories (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE tags (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE
);

-- ========== POSTS ==========
CREATE TABLE posts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  body_mdx TEXT NOT NULL,
  body_text TEXT,
  posted_date DATE NOT NULL,
  last_updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  status post_status NOT NULL DEFAULT 'published',
  category_id BIGINT REFERENCES categories(id) ON DELETE SET NULL,
  search_index BOOLEAN NOT NULL DEFAULT TRUE,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_posts_posted_date ON posts(posted_date);
CREATE INDEX idx_posts_category_id ON posts(category_id);
CREATE INDEX idx_posts_status ON posts(status);

-- ========== POST TAGS ==========
CREATE TABLE post_tags (
  post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  tag_id BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (post_id, tag_id)
);

-- ========== METRICS / COMMENTS / LIKES ==========
CREATE TABLE post_metrics (
  post_id UUID PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  likes INT NOT NULL DEFAULT 0,
  comments_count INT NOT NULL DEFAULT 0,
  views INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE post_comments (
  id BIGSERIAL PRIMARY KEY,
  post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  author_name TEXT,
  author_email TEXT,
  content TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_post_comments_post_id ON post_comments(post_id);
CREATE INDEX idx_post_comments_created_at ON post_comments(created_at);

CREATE TABLE post_likes (
  post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  PRIMARY KEY (post_id, user_id)
);

-- ========== MUSIC CATALOG ==========
CREATE TABLE artists (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  spotify_id TEXT NOT NULL UNIQUE,
  genres JSONB NOT NULL DEFAULT '[]'::jsonb,
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
  photo_url TEXT,
  popularity INTEGER,
  followers BIGINT,
  spotify_url TEXT,
  views INT NOT NULL DEFAULT 0,
  ext_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_artists_views_nonneg CHECK (views >= 0)
);

CREATE TABLE albums (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  release_date DATE,
  cover_url TEXT,
  album_type TEXT,
  spotify_id TEXT NOT NULL UNIQUE,
  ext_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
  total_tracks INT,
  label TEXT,
  popularity INT,
  views INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_albums_views_nonneg CHECK (views >= 0)
);

CREATE TABLE album_artists (
  album_id UUID NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  artist_id UUID NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
  role TEXT,
  PRIMARY KEY (album_id, artist_id)
);

CREATE TABLE tracks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  album_id UUID NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  track_no INT,
  duration_sec INT,
  spotify_id TEXT NOT NULL UNIQUE,
  ext_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
  views INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_tracks_duration_nonneg CHECK (duration_sec IS NULL OR duration_sec >= 0),
  CONSTRAINT chk_tracks_trackno_pos CHECK (track_no IS NULL OR track_no > 0),
  CONSTRAINT chk_tracks_views_nonneg CHECK (views >= 0)
);
CREATE INDEX idx_tracks_album_id ON tracks(album_id);
CREATE INDEX idx_tracks_track_no ON tracks(track_no);

CREATE TABLE track_artists (
  track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  artist_id UUID NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
  role TEXT,
  PRIMARY KEY (track_id, artist_id)
);

-- ========== OUTBOX / PUBLISHING ==========
CREATE TABLE outbox_events (
  id BIGSERIAL PRIMARY KEY,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  processed_at TIMESTAMP,
  retry_count INT NOT NULL DEFAULT 0
);
CREATE INDEX idx_outbox_processed_at ON outbox_events(processed_at);
CREATE INDEX idx_outbox_unprocessed ON outbox_events(processed_at) WHERE processed_at IS NULL;

CREATE TABLE publishing_runs (
  id BIGSERIAL PRIMARY KEY,
  post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  commit_sha TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  triggered_at TIMESTAMP NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMP
);
CREATE INDEX idx_publishing_runs_post_id ON publishing_runs(post_id);
CREATE INDEX idx_publishing_runs_triggered_at ON publishing_runs(triggered_at);

-- ========== OPTIONAL: 운영 로그 ==========
CREATE TABLE op_logs (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMP NOT NULL DEFAULT NOW(),
  name TEXT NOT NULL,
  status_code INT,
  note TEXT
);
CREATE INDEX idx_op_logs_occurred_at ON op_logs(occurred_at);

CREATE INDEX idx_artists_popularity_followers_views
ON artists (popularity DESC, followers DESC, views DESC);

CREATE INDEX idx_albums_popularity_views
ON albums (popularity DESC, views DESC);

