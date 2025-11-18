from app.domain.schemas import AlbumItem

class AlbumCandidateMapper:
    @staticmethod
    def to_list(raw_albums: list[dict]) -> list[AlbumItem]:
        items: list[AlbumItem] = []
        for a in raw_albums:
            first_artist = (a.get("artists") or [{}])[0]
            images = a.get("images") or []
            cover_url = images[0].get("url") if images else None
            external_url = (a.get("external_urls") or {}).get("spotify")

            items.append(
                AlbumItem(
                    id="",  # 아직 DB에 없음
                    title=a.get("name"),
                    release_date=a.get("release_date"),
                    cover_url=cover_url,
                    album_type=a.get("album_type"),
                    spotify_id=a.get("id"),
                    artist_name=first_artist.get("name"),
                    artist_spotify_id=first_artist.get("id"),
                    external_url=external_url,
                    total_tracks=a.get("total_tracks"),
                    label=a.get("label"),
                    popularity=a.get("popularity"),
                )
            )
        return items