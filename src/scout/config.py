from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    llm_provider: str = Field(default="anthropic", alias="LLM_PROVIDER")
    poe_api_key: str = Field(default="", alias="POE_API_KEY")
    poe_drafting_bot: str = Field(default="Claude-Opus-4.7", alias="POE_DRAFTING_BOT")
    poe_scout_bot: str = Field(default="Claude-Opus-4.7-Search", alias="POE_SCOUT_BOT")
    poe_normaliser_bot: str = Field(default="Claude-Sonnet-4.6", alias="POE_NORMALISER_BOT")

    scout_model: str = Field(default="claude-opus-4-7", alias="SCOUT_MODEL")
    scout_effort: str = Field(default="high", alias="SCOUT_EFFORT")
    normaliser_model: str = Field(default="claude-sonnet-4-6", alias="NORMALISER_MODEL")

    max_web_searches: int = Field(default=25, alias="SCOUT_MAX_WEB_SEARCHES")
    max_input_tokens: int = Field(default=200_000, alias="SCOUT_MAX_INPUT_TOKENS")
    max_output_tokens: int = Field(default=50_000, alias="SCOUT_MAX_OUTPUT_TOKENS")

    cron_day_of_week: str = Field(default="mon", alias="SCOUT_CRON_DAY_OF_WEEK")
    cron_hour: int = Field(default=7, alias="SCOUT_CRON_HOUR")
    cron_minute: int = Field(default=0, alias="SCOUT_CRON_MINUTE")

    data_dir: Path = Field(default=ROOT / "data", alias="SCOUT_DATA_DIR")
    prompts_dir: Path = Field(default=ROOT / "prompts", alias="SCOUT_PROMPTS_DIR")
    db_path: Path = Field(default=ROOT / "data" / "scout.db", alias="SCOUT_DB_PATH")

    log_level: str = Field(default="INFO", alias="SCOUT_LOG_LEVEL")
    log_json_path: Path = Field(default=ROOT / "data" / "scout.log", alias="SCOUT_LOG_JSON_PATH")

    @property
    def migrations_dir(self) -> Path:
        return ROOT / "migrations"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def drafts_dir(self) -> Path:
        return self.data_dir / "drafts"

    @property
    def artist_dir(self) -> Path:
        return self.data_dir / "artist"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.runs_dir, self.drafts_dir, self.artist_dir):
            d.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
