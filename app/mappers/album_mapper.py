from __future__ import annotations

from app.domain.schemas import AlbumItem


class AlbumItemMapper:
    @staticmethod
    def to_list(albums, primary_map):
        result: list[AlbumItem] = []
        for al in albums:
            # primary_map keys are str(album_uuid) (see AlbumRepository.
            # get_primary_artist_map). al.id is a uuid.UUID object, so look it up
            # by str() too — a raw-UUID lookup misses every row and silently drops
            # artist_name to None for the whole unified-search album bucket.
            artist_name, artist_sid = (primary_map.get(str(al.id)) or (None, None))

            ext_refs = getattr(al, "ext_refs", {}) or {}
            external_url = ext_refs.get("spotify_url")

            result.append(
                AlbumItem(
                    id=str(al.id),
                    title=al.title,
                    release_date=al.release_date.isoformat() if al.release_date else None,
                    cover_url=al.cover_url,
                    album_type=al.album_type,
                    spotify_id=al.spotify_id,
                    artist_name=artist_name,
                    artist_spotify_id=artist_sid,
                    external_url=external_url,
                    total_tracks=getattr(al, "total_tracks", None),
                    label=getattr(al, "label", None),
                    popularity=getattr(al, "popularity", None),
                    best_new=bool(getattr(al, "best_new", False)),
                )
            )
        return result