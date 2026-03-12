from __future__ import annotations

from app.domain.schemas import TrackItem


class TrackItemMapper:
    @staticmethod
    def to_list(tracks) -> list[TrackItem]:
        out: list[TrackItem] = []
        for t in tracks:
            al = getattr(t, "album", None)

            # 대표 아티스트: 트랙 artists 우선, 없으면 앨범 artists의 첫 번째
            artist_name = None
            track_artists = getattr(t, "artists", None) or []
            if track_artists:
                artist_name = track_artists[0].name
            else:
                album_artists = getattr(al, "artists", None) or []
                if album_artists:
                    artist_name = album_artists[0].name

            out.append(
                TrackItem(
                    id=str(t.id),
                    title=t.title,
                    track_no=t.track_no,
                    duration_sec=t.duration_sec,
                    spotify_id=getattr(t, "spotify_id", None),
                    album_id=str(t.album_id),  # ✅ 클릭 시 앨범 상세로 이동 키
                    album_title=getattr(al, "title", None) if al else None,
                    cover_url=getattr(al, "cover_url", None) if al else None,
                    release_date=al.release_date.isoformat() if al and al.release_date else None,
                    album_spotify_id=getattr(al, "spotify_id", None) if al else None,
                    artist_name=artist_name,
                )
            )
        return out