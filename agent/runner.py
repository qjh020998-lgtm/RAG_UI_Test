"""Agent 运行器：统一入口，处理任务分发与执行。"""
from __future__ import annotations

import os

from langchain_core.messages import AIMessage

from agent.agents import ERPAgentHub
from agent.base import AgentType
from utils.logger import get_logger

logger = get_logger(__name__)


class AgentRunner:
    """Agent 运行主程序。

    职责：
    1. 持有 ERPAgentHub（Agent 工厂 + 意图识别）
    2. 接收用户任务，分发到对应 Agent
    3. 执行并返回结果
    """

    def __init__(self, max_steps: int | None = None) -> None:
        """初始化 Runner。

        Args:
            max_steps: 最大 graph 步数；不传时读取 ``AGENT_MAX_STEPS``，默认 20。
        """
        self.hub = ERPAgentHub()
        if max_steps is None:
            max_steps = int(os.getenv("AGENT_MAX_STEPS", "20"))
        self.max_steps = max_steps

    async def run(self, task: str) -> dict:
        """执行任务。

        Args:
            task: 用户任务描述

        Returns:
            dict: {
                "agent_type": str,      # 执行的 Agent 类型
                "content": str,         # Agent 最终回答
                "status": str,          # "success" | "error"
            }
        """
        agent = None
        try:
            # 1. 意图识别 + 执行
            agent = self.hub.dispatch(task)
            result = await agent.run(task, recursion_limit=self.max_steps)
            agent_type = agent.agent_type.value
            logger.info(f"识别到的Agent是{agent_type}")
            content = result.content if isinstance(result, AIMessage) else str(result)
            logger.info(f"运行成功，结果为{content}")

            return {
                "agent_type": agent_type,
                "content": content,
                "status": "success",
            }


        except Exception as exc:
            logger.exception("任务执行失败：%s", task)
            return {
                "agent_type": agent.agent_type.value if agent is not None else "unknown",
                "content": f"执行失败：{exc}",
                "status": "error",
            }

    async def run_with_agent_type(self, task: str, agent_type: AgentType) -> dict:
        """显式指定 Agent 类型执行任务。

        Args:
            task: 用户任务描述
            agent_type: 指定 Agent 类型

        Returns:
            dict: 同 run()
        """
        try:
            agent = self.hub.get_agent(agent_type)
            result = await agent.run(task, recursion_limit=self.max_steps)

            content = result.content if isinstance(result, AIMessage) else str(result)

            return {
                "agent_type": agent_type.value,
                "content": content,
                "status": "success",
            }

        except Exception as exc:
            logger.exception("任务执行失败：%s", task)
            return {
                "agent_type": agent_type.value,
                "content": f"执行失败：{exc}",
                "status": "error",
            }


__all__ = ["AgentRunner"]
