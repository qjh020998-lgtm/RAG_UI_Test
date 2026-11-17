"""测试 Agent 编排器（最小实现）。

流程：Request -> 路由 -> 执行 -> 失败降级。

刻意不做：实体抽取、性能路由、多 Agent 并行、统计/断路器/升级。
"""
from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

from agent.agents import ERPAgentHub, detect_intent
from agent.base import AgentTimeoutError, AgentType
from context import ContextOverflowError
from core.intent_recognizer import IntentCategory, IntentRecognizer
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgentProfile:
    """Agent 的最小角色契约。

    只保留路由说明和后续 prompt / 流水线需要的核心字段；
    当前不注入 system prompt——BaseAgent 已有 system_prompt + Skills，
    重复拼接会稀释指令。
    """

    role: str
    mission: str
    workflow: tuple[str, ...]
    input_contract: tuple[str, ...]
    output_contract: tuple[str, ...]


AGENT_PROFILES: dict[AgentType, AgentProfile] = {
    AgentType.PAGE: AgentProfile(
        role="ERP 页面交互 Agent",
        mission="采集页面控件、执行 UI 操作、调用 HTTP 接口，并把证据写入 artifacts。",
        workflow=("确认目标页面与操作类型", "打开页面并完成登录", "采集 DOM/控件并截图", "输出 JSON 与截图路径"),
        input_contract=("目标模块/页面", "操作类型（list/add/all）", "必要的业务上下文"),
        output_contract=("控件 JSON 路径", "截图路径", "采集结果摘要"),
    ),
    AgentType.CASE: AgentProfile(
        role="测试用例生成 Agent",
        mission="基于控件 JSON 和业务知识，生成可执行的 Excel 测试用例。",
        workflow=("确认目标模块控件已采集", "读取控件 JSON", "生成正常/边界/异常用例", "校验并导出 xlsx"),
        input_contract=("目标模块名", "控件 JSON", "业务规则/知识"),
        output_contract=("xlsx 路径", "用例数量与覆盖说明", "缺失信息说明"),
    ),
    AgentType.CODE: AgentProfile(
        role="UI 自动化代码 Agent",
        mission="把测试用例转换成 pytest + Playwright 可运行代码。",
        workflow=("读取控件与用例", "确定页面对象与操作步骤", "生成 pytest + Playwright 代码", "说明运行方式和依赖"),
        input_contract=("用例文件", "控件 JSON", "目标模块"),
        output_contract=("代码文件路径", "运行命令", "未覆盖/待确认项"),
    ),
    AgentType.GENERAL: AgentProfile(
        role="通用对话与兜底 Agent",
        mission="处理非测试任务，并在专业 Agent 失败时给出可理解的失败说明。",
        workflow=("判断是否属于测试任务", "直接回答或引导用户重新描述", "失败兜底时解释原因与下一步"),
        input_contract=("用户原始问题", "失败原因（兜底时）"),
        output_contract=("简洁回答", "下一步建议", "不编造执行结果"),
    ),
}


@dataclass
class AgentRequest:
    """一次编排请求。

    只保留当前项目真正需要的字段：
    ``task`` / ``thread_id`` / 可选的 ``user_id`` / ``agent_type`` / ``request_id``，
    以及编排器回填的意图信息。不包含 entities、urgency、history。
    """

    task: str
    thread_id: str | None = None
    user_id: str | None = None
    agent_type: AgentType | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)
    intent: str | None = None
    intent_group: str | None = None
    intent_confidence: float = 1.0


@dataclass
class OrchestratorResult:
    """编排器输出，``to_dict`` 兼容现有 runner 的字典习惯。"""

    content: str
    agent_type: str
    status: str  # success | degraded | error
    route_reason: str
    request_id: str
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, str | float]:
        return {
            "content": self.content,
            "agent_type": self.agent_type,
            "status": self.status,
            "route_reason": self.route_reason,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
        }


# 四类测试意图 -> Agent；显式指定 agent_type 时跳过这张表。
_INTENT_ROUTING: dict[IntentCategory, AgentType] = {
    IntentCategory.REQUIREMENT_DECOMPOSE: AgentType.GENERAL,
    IntentCategory.PAGE_CAPTURE: AgentType.PAGE,
    IntentCategory.CASE_GENERATION: AgentType.CASE,
    IntentCategory.CODE_GENERATION: AgentType.CODE,
}

# 每个 Agent 的默认 graph 步数（LangGraph recursion_limit，不是 ReAct 轮数）。
# 可用 AGENT_MAX_STEPS_<TYPE> 覆盖，例如 AGENT_MAX_STEPS_CODE=60。
DEFAULT_AGENT_MAX_STEPS: dict[AgentType, int] = {
    AgentType.GENERAL: 10,
    AgentType.PAGE: 40,
    AgentType.CASE: 40,
    AgentType.CODE: 60,
}
DEFAULT_MAX_STEPS = 20


def _env_positive_int(name: str) -> int | None:
    """读取正整数环境变量；未设置返回 None，非法值直接报错。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数，当前值：{raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0，当前值：{value}")
    return value


class AgentOrchestrator:
    """最小编排器：路由 + 执行 + GeneralAgent 降级。

    最大步数优先级：显式 ``max_steps`` > ``AGENT_MAX_STEPS_<TYPE>`` >
    ``DEFAULT_AGENT_MAX_STEPS`` > ``AGENT_MAX_STEPS`` > 20。
    显式 ``max_steps`` 会对所有 Agent 生效，便于测试或统一限流。
    """

    def __init__(
        self,
        hub: ERPAgentHub | None = None,
        *,
        max_steps: int | None = None,
        recognizer: IntentRecognizer | None = None,
    ) -> None:
        self.hub = hub or ERPAgentHub()
        global_default = _env_positive_int("AGENT_MAX_STEPS") or DEFAULT_MAX_STEPS
        self._max_steps_override = max_steps
        # 保留原属性：未显式传入时是全局兜底值，便于外部读取/兼容旧代码。
        self.max_steps = max_steps if max_steps is not None else global_default
        self.step_limits: dict[AgentType, int] = {
            agent_type: (
                max_steps
                if max_steps is not None
                else (
                    _env_positive_int(
                        f"AGENT_MAX_STEPS_{agent_type.value.upper()}"
                    )
                    or DEFAULT_AGENT_MAX_STEPS.get(agent_type)
                    or global_default
                )
            )
            for agent_type in AgentType
        }
        self.recognizer = recognizer or IntentRecognizer()

    def max_steps_for(self, agent_type: AgentType) -> int:
        """返回某个 Agent 本次执行使用的 graph 步数。"""
        return self.step_limits.get(agent_type, self.max_steps)

    async def run(self, request: AgentRequest | str) -> OrchestratorResult:
        """执行一次请求；字符串是 ``AgentRequest(task=...)`` 的简写。"""
        req = request if isinstance(request, AgentRequest) else AgentRequest(task=request)
        if not req.task or not req.task.strip():
            raise ValueError("task 不能为空")

        started = time.perf_counter()
        agent_type, route_reason = await self._route(req)

        try:
            content = await self._call(
                agent_type, req.task, req.thread_id, req.user_id
            )
            status = "success"
        except ContextOverflowError as exc:
            # 上下文超限是输入问题，降级调用没有意义，直接报错。
            logger.warning("[Orchestrator] 上下文超限，停止执行：%s", exc)
            content = str(exc)
            status = "error"
            route_reason = f"{route_reason}；上下文超限，未执行"
        except AgentTimeoutError as exc:
            logger.warning("[Orchestrator] %s 执行超时：%s", agent_type.value, exc)
            content = str(exc)
            status = "error"
            route_reason = f"{route_reason}；执行超时"
        except Exception as exc:
            logger.exception("[Orchestrator] %s 执行失败", agent_type.value)
            if agent_type is AgentType.GENERAL:
                content = f"执行失败：{exc}"
                status = "error"
                route_reason = f"{route_reason}；GeneralAgent 执行失败"
            else:
                content, fallback_ok = await self._fallback(req, exc)
                status = "degraded" if fallback_ok else "error"
                agent_type = AgentType.GENERAL
                route_reason = (
                    f"{route_reason}；失败后降级到 {AGENT_PROFILES[agent_type].role}"
                )

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "[Orchestrator] done | agent=%s status=%s %.1fms | %s",
            agent_type.value,
            status,
            latency_ms,
            route_reason,
        )
        return OrchestratorResult(
            content=content,
            agent_type=agent_type.value,
            status=status,
            route_reason=route_reason,
            request_id=req.request_id,
            latency_ms=latency_ms,
        )

    async def astream(self, request: AgentRequest | str) -> AsyncIterator[dict[str, Any]]:
        """流式执行：meta -> progress* -> delta* -> done/error。"""
        req = request if isinstance(request, AgentRequest) else AgentRequest(task=request)
        if not req.task or not req.task.strip():
            raise ValueError("task 不能为空")

        agent_type, route_reason = await self._route(req)
        yield {
            "type": "meta",
            "agent_type": agent_type.value,
            "route_reason": route_reason,
            "request_id": req.request_id,
        }

        chunks: list[str] = []
        try:
            agent = self.hub.get_agent(agent_type)
            stream_kwargs: dict[str, Any] = {
                "recursion_limit": self.max_steps_for(agent_type),
                "thread_id": req.thread_id,
            }
            if req.user_id:
                stream_kwargs["user_id"] = req.user_id
            async for event in agent.astream(req.task, **stream_kwargs):
                if event["type"] == "progress":
                    yield event
                elif event["type"] == "delta":
                    chunks.append(event["text"])
                    yield event
                elif event["type"] == "done":
                    yield {
                        "type": "done",
                        "content": event["content"],
                        "status": "success",
                        "agent_type": agent_type.value,
                        "route_reason": route_reason,
                        "request_id": req.request_id,
                    }
                    return
        except ContextOverflowError as exc:
            logger.warning("[Orchestrator] 上下文超限，停止流式执行：%s", exc)
            yield {
                "type": "error",
                "content": str(exc),
                "status": "error",
                "agent_type": agent_type.value,
                "route_reason": f"{route_reason}；上下文超限，未执行",
                "request_id": req.request_id,
            }
            return
        except AgentTimeoutError as exc:
            logger.warning("[Orchestrator] %s 流式执行超时：%s", agent_type.value, exc)
            yield {
                "type": "error",
                "content": str(exc),
                "partial": "".join(chunks),
                "status": "error",
                "agent_type": agent_type.value,
                "route_reason": f"{route_reason}；执行超时",
                "request_id": req.request_id,
            }
            return
        except Exception as exc:
            if chunks:
                yield {
                    "type": "error",
                    "content": f"执行中断：{exc}",
                    "partial": "".join(chunks),
                    "agent_type": agent_type.value,
                    "route_reason": route_reason,
                    "request_id": req.request_id,
                }
                return

            content, fallback_ok = await self._fallback(req, exc)
            fallback_reason = (
                f"{route_reason}；失败后降级到 "
                f"{AGENT_PROFILES[AgentType.GENERAL].role}"
            )
            if fallback_ok:
                yield {"type": "delta", "text": content}
                yield {
                    "type": "done",
                    "content": content,
                    "status": "degraded",
                    "agent_type": AgentType.GENERAL.value,
                    "route_reason": fallback_reason,
                    "request_id": req.request_id,
                }
            else:
                yield {
                    "type": "error",
                    "content": content,
                    "status": "error",
                    "agent_type": AgentType.GENERAL.value,
                    "route_reason": fallback_reason,
                    "request_id": req.request_id,
                }
            return

        yield {
            "type": "error",
            "content": "Agent 没有返回完整结果",
            "agent_type": agent_type.value,
            "route_reason": route_reason,
            "request_id": req.request_id,
        }

    async def _route(self, req: AgentRequest) -> tuple[AgentType, str]:
        """显式指定优先；否则用测试场景意图，未命中回退关键词路由。"""
        if req.agent_type is not None:
            if not isinstance(req.agent_type, AgentType):
                raise ValueError(f"未知 AgentType：{req.agent_type}")
            req.intent_group = req.agent_type.value
            return req.agent_type, f"显式指定 {AGENT_PROFILES[req.agent_type].role}"

        try:
            result = await self.recognizer.recognize(req.task)
        except ValueError:
            agent_type = detect_intent(req.task)
            req.intent_group = agent_type.value
            req.intent_confidence = 0.0
            return (
                agent_type,
                f"测试场景意图未命中，回退关键词路由 → {AGENT_PROFILES[agent_type].role}",
            )

        req.intent = result.intent.value
        req.intent_group = result.intent_group
        req.intent_confidence = result.confidence
        agent_type = _INTENT_ROUTING[result.intent]
        return (
            agent_type,
            f"意图 {result.intent.value}（{result.confidence:.2f}）→ "
            f"{AGENT_PROFILES[agent_type].role}",
        )

    async def _call(
        self,
        agent_type: AgentType,
        task: str,
        thread_id: str | None,
        user_id: str | None = None,
    ) -> str:
        agent = self.hub.get_agent(agent_type)
        kwargs: dict[str, Any] = {
            "recursion_limit": self.max_steps_for(agent_type),
            "thread_id": thread_id,
        }
        # 兼容只实现 thread_id 的测试替身；真实 Agent 会用它隔离用户画像。
        if user_id:
            kwargs["user_id"] = user_id
        message = await agent.run(task, **kwargs)
        return str(message.content) if isinstance(message, AIMessage) else str(message)

    async def _fallback(self, req: AgentRequest, exc: Exception) -> tuple[str, bool]:
        """专业 Agent 失败后让 GeneralAgent 解释，不重放原任务。"""
        prompt = (
            f"用户任务：{req.task}\n"
            f"执行失败：{str(exc)[:300]}\n"
            "请用中文向用户说明失败原因，并给出下一步可操作建议；不要编造已执行的结果。"
        )
        # 用独立会话隔离兜底对话，避免污染原会话的工作记忆。
        fallback_thread = f"{req.thread_id}:fallback" if req.thread_id else None
        try:
            content = await self._call(
                AgentType.GENERAL, prompt, fallback_thread, req.user_id
            )
            return content, True
        except Exception as fallback_exc:
            logger.exception("[Orchestrator] GeneralAgent 兜底失败")
            return f"执行失败：{exc}；兜底也失败：{fallback_exc}", False


__all__ = [
    "AGENT_PROFILES",
    "DEFAULT_AGENT_MAX_STEPS",
    "DEFAULT_MAX_STEPS",
    "AgentOrchestrator",
    "AgentProfile",
    "AgentRequest",
    "OrchestratorResult",
]
