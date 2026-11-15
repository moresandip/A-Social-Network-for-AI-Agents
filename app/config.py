from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./agent_network.db"
    openai_api_key: str = ""
    webhook_secret: str = "dev-webhook-secret-change-me"

    routing_top_k: int = 50
    routing_final_limit: int = 5
    routing_rerank_threshold: float = 0.55
    embedding_dim: int = 384

    # Demo mock-agent base URL (platform notifies these locally)
    mock_agent_base_url: str = "http://127.0.0.1:8000/mock-agents"

    @property
    def use_openai(self) -> bool:
        return bool(self.openai_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
