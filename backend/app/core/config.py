from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENV: str = "dev"
    SECRET_KEY: str
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"

settings = Settings()
