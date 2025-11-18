from app.domain.schemas import ArtistItem

class ArtistItemMapper:
    @staticmethod
    def _normalize_genres(raw) -> list[str]:
        if raw is None:
            return []
        # DB에 jsonb(list)로 들어온 경우
        if isinstance(raw, list):
            return [str(g) for g in raw]
        # TEXT에 JSON 문자열로 저장된 경우
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [str(g) for g in data]
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def to_list(artists):
        items: list[ArtistItem] = []
        for a in artists:
            ext_refs = getattr(a, "ext_refs", {}) or {}
            spotify_url = getattr(a, "spotify_url", None) or ext_refs.get("spotify_url")

            items.append(
                ArtistItem(
                    id=str(a.id),
                    name=a.name,
                    spotify_id=a.spotify_id,
                    cover_url=a.photo_url,
                    genres=ArtistItemMapper._normalize_genres(getattr(a, "genres", None)),
                    follower_count=getattr(a, "follower_count", None),
                    popularity=getattr(a, "popularity", None),
                    spotify_url=spotify_url,
                )
            )
        return items