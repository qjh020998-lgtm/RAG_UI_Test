from functools import lru_cache

from pydantic import BaseSettings

class Settings(BaseSettings):

    # LLM 配置项
    LLM_API_KEY: str
    LLM_BASE_URL: str
    primary_model: str = "qwen3.8-flash"
    fallback_model: str = "qwen3.8-max"

    # langsmith 配置项
    langsmith_tracing_v2: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "project_api"

    # 应用层面
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit: str = "20/minute"
    cache_ttl_seconds: int = 300
    max_retries: int = 3

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache()  # 各种设置只需要加载一次就可以重复使用
def get_settings() -> Settings:
    """实现单例模式， 不用每一次请求都读取.env文件"""
    return Settings()