"""App settings (from .env) and search preferences (from config/search.yaml)."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Secrets and environment settings. Values come from .env, never from code."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'jobs.db'}"
    search_config_path: Path = PROJECT_ROOT / "config" / "search.yaml"
    master_resume_path: Path = PROJECT_ROOT / "private" / "master_resume.json"
    log_level: str = "INFO"

    # Job sources
    adzuna_app_id: str | None = None
    adzuna_app_key: SecretStr | None = None  # SecretStr hides the value if it's ever printed


class SearchConfig(BaseModel):
    """What the agent should look for. Edit config/search.yaml to change it."""

    roles: list[str] = Field(min_length=1)
    locations: list[str] = Field(min_length=1)
    include_remote: bool = True
    min_experience_years: int = Field(default=0, ge=0)
    max_experience_years: int = Field(default=3, ge=0)
    min_salary_inr: int | None = Field(default=None, ge=0)
    must_have_skills: list[str] = []
    deal_breakers: list[str] = []
    daily_application_cap: int = Field(default=15, ge=1, le=50)
    min_match_score: float = Field(default=40, ge=0, le=100)

    @model_validator(mode="after")
    def check_experience_range(self) -> "SearchConfig":
        if self.min_experience_years > self.max_experience_years:
            raise ValueError("min_experience_years cannot be more than max_experience_years")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_search_config(path: Path | None = None) -> SearchConfig:
    path = path or get_settings().search_config_path
    with open(path, encoding="utf-8") as f:
        return SearchConfig.model_validate(yaml.safe_load(f))
