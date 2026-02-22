from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "ClipFlow AI"
    environment: str = "development"
    database_url: str
    youtube_api_key: str  # <--- וודא שהשורה הזו קיימת כאן

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # <--- זה ימנע מהשרת לקרוס אם יש משתנים נוספים ב-env
    )

settings = Settings()