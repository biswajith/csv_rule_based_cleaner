"""Settings from the environment / `.env` (spec §12)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env into os.environ so provider packages (OPENAI_API_KEY) see it.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = None
    llm_model: str = "openai:gpt-5-mini"
    # The single planning call uses a stronger model ("Luna 6"); empty falls back to llm_model.
    planner_model: str | None = "openai:gpt-6-luna"
    llm_model_kwargs: dict[str, Any] = {}
    llm_timeout: float = 180
    llm_max_retries: int = 2
    sample_size: int = 1000
    batch_size: int = 1000
    llm_cells_per_call: int = 40
    llm_concurrency: int = 4
    max_llm_cells: int = 5000
    llm_max_value_chars: int = 2000
    planner_max_repairs: int = 2
    planner_max_profile_chars: int = 150_000
    output_dir: str = "output"
    upload_dir: str = "data/uploads"
    max_upload_mb: int = 200
    web_dist: Path | None = Path(__file__).resolve().parents[3] / "web" / "dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
