from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "music-backend"
    ENV: str = "local"

    # DB
    DATABASE_URL: str

    # Spotify
    SPOTIFY_CLIENT_ID: str
    SPOTIFY_CLIENT_SECRET: str
    SPOTIFY_TOKEN_URL: str = "https://accounts.spotify.com/api/token"
    SPOTIFY_API_BASE: str = "https://api.spotify.com/v1"
    SPOTIFY_DEFAULT_MARKET: str = "KR"

    # AWS / SQS
    AWS_DEFAULT_REGION: str = "ap-northeast-2"
    LOCALSTACK_ENDPOINT: str | None = None        # local이면 http://localhost:4566
    AWS_ACCOUNT_ID: str | None = None             # local이면 000000000000
    QUEUE_NAME: str = "test-queue"
    SQS_QUEUE_URL: str | None = None              # 있으면 이 값 우선

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()