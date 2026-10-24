"""Agent 基础设施：配置、模型、日志、调用入口。

三个 Agent（控件获取 / 步骤生成 / 最终编排）都继承 ``BaseAgent``，共享：

- ``.env`` 里的 LLM 配置（``LLM_API_KEY`` / ``LLM_BASE_URL`` / 模型名）
- Litellm 模型实例（快 / 强两档）
- 统一的日志与 ``run()`` 入口
- ``@tool`` 工具注册方式

为什么用 LitellmModel
---------------------
项目已经用 ``agents``（OpenAI Agents SDK），``LitellmModel`` 可以通过
``dashscope/`` 前缀直接路由到百炼，不需要改 SDK。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, Runner
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 只加载一次；已存在的环境变量优先，便于测试时临时覆盖
load_dotenv(PROJECT_ROOT / ".env", override=False)

log = logging.getLogger("agent")
if not log.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@dataclass(frozen=True)
class AgentSettings:
    """从 ``.env`` 读取的 Agent 配置。"""

    api_key: str
    base_url: str
    fast_model: str
    strong_model: str
    temperature: float
    max_tokens: int
    max_turns: int

    @classmethod
    def from_env(cls) -> "AgentSettings":
        return cls(
            api_key=os.getenv("LLM_API_KEY", "").strip(),
            base_url=os.getenv(
                "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            fast_model=os.getenv("LLM_FAST_MODEL", "qwen3.8-flash").strip(),
            strong_model=os.getenv("LLM_STRONG_MODEL", "qwen3.8-max").strip(),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            max_turns=int(os.getenv("AGENT_MAX_TURNS", "8")),
        )

    def require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY 为空：请在根目录 .env 中填写后重试")


def build_model(strong: bool = False, settings: AgentSettings | None = None) -> LitellmModel:
    """构建 Litellm 模型；``strong=True`` 用 qwen3.8-max，否则用 flash。"""
    settings = settings or AgentSettings.from_env()
    settings.require_key()

    # litellm 的 dashscope provider 会读 DASHSCOPE_API_KEY
    os.environ.setdefault("DASHSCOPE_API_KEY", settings.api_key)

    model = settings.strong_model if strong else settings.fast_model
    return LitellmModel(model=f"dashscope/{model}", api_key=settings.api_key)


class BaseAgent:
    """所有 Agent 的基类：统一模型、日志、调用入口。

    子类只需定义 ``name`` / ``instructions``，需要工具时在 ``__init__``
    里传 ``tools=[...]``。
    """

    name = "base"
    instructions = "You are a helpful assistant."

    def __init__(self, *, strong: bool = False, tools=None):
        self.settings = AgentSettings.from_env()
        self.model = build_model(strong=strong, settings=self.settings)
        self.tools = list(tools or [])
        self.agent = Agent(
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            tools=self.tools,
        )

    def run(self, prompt: str) -> str:
        """同步执行一次 Agent 任务，返回最终文本。"""
        log.info("[Agent] %s 被调用", self.name)
        result = asyncio.run(
            Runner.run(self.agent, prompt, max_turns=self.settings.max_turns)
        )
        output = (result.final_output or "").strip()
        log.info("[Agent] %s 完成（输出 %s 字符）", self.name, len(output))
        return output


if __name__ == "__main__":
    # 自检：确认 .env 与模型可用
    settings = AgentSettings.from_env()
    print("api_key set:", bool(settings.api_key))
    print("fast:", settings.fast_model, "| strong:", settings.strong_model)
    print("max_turns:", settings.max_turns)
