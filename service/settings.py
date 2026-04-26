"""Service-level configuration loaded from environment."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    simulator_base_url: str = "http://localhost:8000"
    candidate_token: str = ""

    control_plane_token: str = "change-me-in-production"

    app_db_path: str = "app.db"
    audit_db_path: str = "audit.db"
    policy_file: str = "policy_and_faq.md"
    policy_index_path: str = "policy_index.pkl"

    simulated_now: str = "2026-04-13"

    embeddings_provider: str = "sentence-transformers"
    embeddings_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    max_prod_sessions_per_run: int = 22
    prod_session_cap_total: int = 44

    agent_version: str = os.environ.get("AGENT_VERSION", "dev")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
