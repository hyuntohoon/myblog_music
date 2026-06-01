# myblog_music

> **MyBlog + Music Review** 프로젝트의 음악 도메인 API — DB-first 검색 + Spotify 후보(candidates) + SQS 비동기 동기화 트리거

🔗 **전체 프로젝트 README:** [MyBlog + Music Review](https://github.com/hyuntohoon/myblog_front#관련-리포지토리)

---

## 개요

음악 검색·조회 API와 Spotify 동기화 트리거를 담당합니다. **"검색은 DB로 안정적으로, 최신화는 필요할 때만 비동기로"** 라는 핵심 설계를 서비스 경계로 구현한 리포입니다.

---

## 핵심 설계

```
[사용자 검색] → DB-first 검색 (안정적, 저비용)
[Sync 클릭]  → Spotify 후보 즉시 응답 + SQS enqueue (비동기 동기화)
[상세 조회]  → DB-only (단일 소스, 일관성)
```

- **기본 검색은 DB에서 완결** — Spotify 장애·429가 검색 UX에 영향 없음
- **Sync 버튼은 사용자 의도 기반** — 불필요한 외부 호출 비용 제거
- **candidates 즉시 응답 + Worker 비동기 저장** — 응답 지연과 데이터 정합성 분리

---

## API 엔드포인트

| Method | Path                                          | 설명                                              | 인증        |
|--------|-----------------------------------------------|---------------------------------------------------|-------------|
| `GET`  | `/api/music/search/unified`                   | DB-first 통합 검색 (Artists/Albums/Tracks)        | -           |
| `GET`  | `/api/music/search/candidates`                | Spotify 후보 검색 + SQS enqueue                   | Cognito JWT |
| `GET`  | `/api/music/albums/:id`                       | 앨범 상세 (DB-only)                               | -           |
| `GET`  | `/api/music/albums/by-spotify/:spotify_id`    | Spotify ID 로 앨범 조회 (DB-only)                 | -           |
| `GET`  | `/api/music/artists/:artist_id`               | 아티스트 hero (followers, genres, popularity)     | -           |
| `GET`  | `/api/music/artists/:artist_id/albums`        | 해당 아티스트의 앨범 목록                         | -           |
| `GET`  | `/api/music/artists/:artist_id/top-tracks`    | 해당 아티스트의 인기 트랙                         | -           |

---

## 요청 흐름

### DB-first 검색 (기본)

```
사용자 → GET /search/unified?q=radiohead
       → Music API가 DB에서 ILIKE 검색
       → 결과 반환 (Spotify 호출 없음)
```

### Sync 버튼 (최신화)

```
사용자 → GET /search/candidates?q=radiohead
       → Music API가 Spotify API에 검색
       → ✅ candidates 즉시 응답 (사용자에게)
       → SQS에 앨범 ID 배치 메시지 enqueue (최대 20개/메시지)
       → Worker가 백그라운드에서 DB 동기화
```

---

## 기술 스택

| 항목         | 기술                              |
|--------------|-----------------------------------|
| 배포         | AWS Lambda + API Gateway          |
| 데이터베이스 | Neon Serverless Postgres          |
| 비동기 큐    | Amazon SQS (album-sync FIFO + DLQ)|
| 외부 API     | Spotify Web API                   |
| 도메인 모델  | `myblog-shared-db` (git-pinned)   |

---

## 환경 변수

| 변수                    | 설명                                                                |
|-------------------------|---------------------------------------------------------------------|
| `SECRETS_ARN`           | AWS Secrets Manager `myblog/music` 의 ARN (prod). cold-start 1회 fetch + `@lru_cache` |
| `DATABASE_URL`          | Neon 접속 URL (`postgresql+psycopg://...`) — local dev 시 직접 주입 |
| `SPOTIFY_CLIENT_ID`     | Spotify 앱 Client ID                                                |
| `SPOTIFY_CLIENT_SECRET` | Spotify 앱 Client Secret                                            |
| `SQS_QUEUE_URL`         | SQS 큐 URL (album-sync FIFO)                                        |
| `AWS_DEFAULT_REGION`    | AWS 리전                                                            |

> 로컬 개발 시 리포 루트에 `.env` (git-ignored)를 만들어 채웁니다. 실제 값은 절대 커밋하지 마세요 — 운영 값은 모두 `SECRETS_ARN` 한 곳에서 로드됩니다.
>
> ```dotenv
> DATABASE_URL=postgresql+psycopg://blog:blog@127.0.0.1:5433/blog
> SPOTIFY_CLIENT_ID=your-spotify-client-id
> SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
> ```

---

## 왜 분리했는가

외부 API(Spotify)와 연결되는 영역은 **장애·레이트리밋·비용** 이슈가 있어 블로그 core API와 격리해야 합니다. `candidates` 엔드포인트는 side-effect(SQS enqueue)가 있어 운영 정책(레이트리밋, 관측)도 다릅니다. **"검색은 DB로, 최신화는 비동기"** 라는 아키텍처 결정을 서비스 경계로 명확히 반영했습니다.

---

## 관련 리포지토리

| 리포                                                                   | 역할                                  |
|------------------------------------------------------------------------|---------------------------------------|
| [`myblog_front`](https://github.com/hyuntohoon/myblog_front)           | 정적 사이트 + 글쓰기 UI               |
| [`myblog_backend`](https://github.com/hyuntohoon/myblog_backend)       | 글·카테고리 API + 인증 + 발행         |
| **myblog_music** (현재)                                                | DB-first 검색 + Sync 트리거           |
| [`myblog_worker`](https://github.com/hyuntohoon/myblog_worker)         | SQS Consumer + Spotify 동기화         |
| [`myblog_shared_db`](https://github.com/hyuntohoon/myblog_shared_db)   | 공유 SQLAlchemy 모델 (git-pinned)     |

> 옛 `myblog_publish` 서비스는 ARCH-11 으로 backend 에 흡수되었고 업스트림은 archived 됨.
