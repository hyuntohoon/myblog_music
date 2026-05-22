-- Migration: 001_tracks_spotify_id_unique
-- Purpose:   Add UNIQUE constraint on tracks.spotify_id (zero-downtime)
-- Covers:    BUG-3 migration part (myblog_music PR-6)
-- Canonical: docs/contracts/schema.sql already declares spotify_id NOT NULL UNIQUE
--
-- Run order:
--   1. Run STEP 1 in psql — review the duplicate report.
--   2. If duplicates exist, run STEP 2 to remove them (review first!).
--   3. Run STEP 3 in psql — creates the index WITHOUT locking the table.
--   4. Run STEP 4 in psql — promotes the index to a constraint.
--
-- Notes:
--   - CONCURRENTLY cannot run inside a transaction block.
--     Run steps 3 and 4 outside of BEGIN/COMMIT.
--   - This migration is idempotent: re-running is safe.
--   - Worker ON CONFLICT (spotify_id) upserts are unaffected once the
--     constraint exists; they rely on it being present.

-- =============================================================================
-- STEP 1 — Identify duplicates (READ-ONLY, safe to run any time)
-- =============================================================================
SELECT
    spotify_id,
    COUNT(*)        AS cnt,
    MIN(created_at) AS oldest,
    MAX(created_at) AS newest,
    array_agg(id ORDER BY created_at DESC) AS ids   -- first id = keep, rest = delete
FROM tracks
WHERE spotify_id IS NOT NULL
GROUP BY spotify_id
HAVING COUNT(*) > 1
ORDER BY cnt DESC;


-- =============================================================================
-- STEP 2 — Remove duplicates (keep the most-recently created row per spotify_id)
-- Only run if STEP 1 returned rows.
-- =============================================================================
BEGIN;

DELETE FROM tracks
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY spotify_id
                ORDER BY created_at DESC, id DESC   -- deterministic tiebreak
            ) AS rn
        FROM tracks
        WHERE spotify_id IS NOT NULL
    ) ranked
    WHERE rn > 1
);

-- Verify: should return 0 rows after deletion
SELECT spotify_id, COUNT(*) FROM tracks GROUP BY spotify_id HAVING COUNT(*) > 1;

COMMIT;


-- =============================================================================
-- STEP 3 — Create unique index concurrently (run OUTSIDE a transaction block)
-- This builds the index without holding an ACCESS EXCLUSIVE lock.
-- =============================================================================
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
    uq_tracks_spotify_id
ON tracks (spotify_id);


-- =============================================================================
-- STEP 4 — Promote index to constraint (run OUTSIDE a transaction block)
-- Links the pre-built index to a formal UNIQUE constraint.
-- =============================================================================
ALTER TABLE tracks
    ADD CONSTRAINT uq_tracks_spotify_id UNIQUE
    USING INDEX uq_tracks_spotify_id;


-- =============================================================================
-- STEP 5 — Verify
-- =============================================================================
SELECT
    conname        AS constraint_name,
    contype        AS type,         -- 'u' = unique
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'tracks'::regclass
  AND conname = 'uq_tracks_spotify_id';
