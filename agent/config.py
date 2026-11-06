"""读取 .env 中的 LLM 配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# 千问（DashScope）与 DeepSeek 的 OpenAI 兼容接口地址。
DEFAULT_BASE_URLS = {
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


class ConfigError(ValueError):
    """配置缺失或格式错误。"""


@dataclass(frozen=True)
class LLMConfig:
    """从 .env 读取的 LLM 配置。"""

    api_key: str = field(repr=False)
    base_url: str
    fast_model: str
    strong_model: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 120.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ConfigError("缺少 LLM_API_KEY（或 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY）")
        if not self.base_url.strip():
            raise ConfigError("缺少 LLM_BASE_URL")
        if not self.fast_model.strip():
            raise ConfigError("缺少 LLM_FAST_MODEL")
        if not 0 <= self.temperature <= 2:
            raise ConfigError("LLM_TEMPERATURE 必须在 0~2 之间")
        if self.max_tokens <= 0:
            raise ConfigError("LLM_MAX_TOKENS 必须大于 0")
        if self.timeout <= 0:
            raise ConfigError("LLM_TIMEOUT 必须大于 0")
        if self.max_retries < 0:
            raise ConfigError("LLM_MAX_RETRIES 不能小于 0")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，当前值：{raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值：{raw!r}") from exc


def detect_provider(model: str, base_url: str = "") -> str:
    """按模型名或 base_url 判断 provider，返回 ``qwen`` 或 ``deepseek``。"""
    text = f"{model} {base_url}".lower()
    return "deepseek" if "deepseek" in text else "qwen"


def load_llm_config(
    env_file: str | Path | None = None,
    *,
    override: bool = False,
) -> LLMConfig:
    """读取 .env 并返回 LLMConfig。

    兼容两家供应商：

    - 千问：``LLM_BASE_URL`` 指向 DashScope 兼容模式；
    - DeepSeek：``LLM_BASE_URL`` 留空时按模型名自动填默认地址。

    ``LLM_API_KEY`` 优先，未设置时回退到 ``DASHSCOPE_API_KEY`` /
    ``DEEPSEEK_API_KEY``。
    """
    path = Path(env_file).expanduser() if env_file is not None else DEFAULT_ENV_FILE
    if env_file is not None and not path.exists():
        raise ConfigError(f".env 文件不存在：{path}")
    if path.exists():
        load_dotenv(dotenv_path=path, override=override)

    fast_model = _env("LLM_FAST_MODEL")
    strong_model = _env("LLM_STRONG_MODEL") or fast_model
    base_url = _env("LLM_BASE_URL")
    provider = detect_provider(fast_model, base_url)
    if not base_url:
        base_url = DEFAULT_BASE_URLS[provider]

    api_key = _env("LLM_API_KEY")
    if not api_key:
        api_key = _env("DASHSCOPE_API_KEY" if provider == "qwen" else "DEEPSEEK_API_KEY")

    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        fast_model=fast_model,
        strong_model=strong_model,
        temperature=_env_float("LLM_TEMPERATURE", 0.2),
        max_tokens=_env_int("LLM_MAX_TOKENS", 4096),
        timeout=_env_float("LLM_TIMEOUT", 120.0),
        max_retries=_env_int("LLM_MAX_RETRIES", 2),
    )