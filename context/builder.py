"""
整合短期记忆、长期记忆以及用户画像，提供统一的接口
短期记忆（history）、长期记忆（memorystore）和用户画像（profile）

一次 ``build`` 的顺序：
1. 预算预检：输入（system + 工作记忆 + 本轮问题）超过上下文窗口阈值直接报错，
   不检索、不压缩、不落库；
2. 工作记忆追加本轮 user 消息；达到 ``COMPRESS_AT`` 时把旧消息合并成摘要
   （旧摘要 + 新对话 -> 一份新摘要），写回同一条情景记忆并裁剪工作记忆；
3. 读取情景记忆（按 user_id 隔离、排除当前会话）和用户画像；
4. 拼成 ``[{"role": ..., "content": ...}]``，组装后仍超限则丢弃情景记忆再校验；
5. 把画像更新丢到后台任务（``UserProfile.schedule_update``），不阻塞返回。
"""

from __future__ import annotations

import inspect
import math
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from memory.store import HISTORY_TOP_K
from utils.logger import get_logger

from .history import COMPRESS_AT, KEEP_RECENT, WORKING_MAX, HistoryManager
from .message import Message

logger = get_logger(__name__)

# 上下文窗口阈值（输入 token 上限），可用环境变量覆盖。
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "8000") or "8000")
SUMMARY_MAX_CHARS = 1200

Summarizer = Callable[[Sequence[Message], str], Awaitable[str] | str]

SUMMARY_PROMPT = """你在维护一段对话的长期记忆摘要（情景记忆）。

要求：
1. 把【已有摘要】和【新增对话】合并成一份新的摘要，只保留对后续对话有用的信息，不要简单拼接。
2. 保留用户目标、已确认的事实/结论、关键实体（模块名、页面、字段、文件路径）、未完成事项。
3. 用中文、条目式、第三人称，控制在 200 字以内。
4. 只输出摘要正文，不要输出解释或 Markdown 代码块。

【已有摘要】
{previous}

【新增对话】
{dialog}"""

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ENCODER: Any = None
_ENCODER_READY = False


def _get_encoder() -> Any:
    """tiktoken 编码器（失败时返回 None，退回字符估算）。"""
    global _ENCODER, _ENCODER_READY
    if not _ENCODER_READY:
        _ENCODER_READY = True
        try:
            import tiktoken

            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # pragma: no cover - 取决于运行环境
            logger.warning("[Context] tiktoken 不可用，改用字符估算 token：%s", exc)
    return _ENCODER


def estimate_tokens(text: str) -> int:
    """估算文本 token 数：优先 tiktoken，缺失时按中英文混合启发式。"""
    if not text:
        return 0

    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:  # pragma: no cover - 编码异常时退回估算
            pass

    cjk = len(_CJK.findall(text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))


class ContextOverflowError(ValueError):
    """输入/上下文超过配置的上下文窗口阈值。"""


def _local_summary(messages: Sequence[Message], previous: str) -> str:
    """模型不可用时的兜底摘要：截断旧摘要 + 截断最近消息，长度有界。"""
    lines: list[str] = []
    for message in messages:
        if message.role == "summary":
            continue
        speaker = "用户" if message.role == "user" else "助手"
        text = " ".join(message.content.split())
        if len(text) > 80:
            text = text[:80] + "…"
        lines.append(f"- {speaker}：{text}")

    body = "\n".join(lines)
    merged = f"【此前摘要】\n{previous.strip()}\n{body}" if previous else body
    return ("（本地摘要，模型不可用）\n" + merged)[:SUMMARY_MAX_CHARS]


class ContextBuilder:
    """把三层记忆拼成发给模型的消息列表。

    ``store`` / ``profile`` 不传时使用默认的 ChromaDB 实现（``memory_db/``），
    并在第一次真正读写时创建；测试可以注入临时目录或假实现。

    ``max_context_tokens`` 默认取环境变量 ``CONTEXT_MAX_TOKENS``（8000），
    输入超过该阈值时在构建前直接抛 :class:`ContextOverflowError`。

    典型用法：
    ```python
    builder = ContextBuilder()
    messages = await builder.build(
        task, user_id=user_id, session_id=thread_id, system_prompt=system_prompt
    )
    reply = await llm.ainvoke(messages)
    builder.add_assistant(thread_id, reply.content)
    await builder.aclose()   # 等待后台画像更新结束
    ```
    """

    def __init__(
        self,
        store: Any = None,
        profile: Any = None,
        *,
        max_context_tokens: int | None = None,
        top_k: int = HISTORY_TOP_K,
        summarizer: Summarizer | None = None,
    ) -> None:
        if max_context_tokens is not None and max_context_tokens <= 0:
            raise ValueError("max_context_tokens 必须大于 0")
        if top_k <= 0:
            raise ValueError("top_k 必须大于 0")
        self._store = store
        self._profile = profile
        self.max_context_tokens = (
            CONTEXT_MAX_TOKENS if max_context_tokens is None else max_context_tokens
        )
        self.top_k = top_k
        self.summarizer = summarizer
        self._histories: dict[str, HistoryManager] = {}

    # ---- 三层记忆的懒加载 ----
    @property
    def store(self) -> Any:
        """情景记忆（ChromaDB ``episodic``）。"""
        if self._store is None:
            from memory.store import MemoryStore

            self._store = MemoryStore()
        return self._store

    @property
    def profile(self) -> Any:
        """用户画像（ChromaDB ``user_profile``）。"""
        if self._profile is None:
            from memory.profile import UserProfile

            self._profile = UserProfile()
        return self._profile

    def history(self, session_id: str) -> HistoryManager:
        """获取会话的工作记忆（内存，会话没了就没了）。"""
        session_id = (session_id or "").strip()
        if not session_id:
            raise ValueError("session_id 不能为空")
        manager = self._histories.get(session_id)
        if manager is None:
            manager = HistoryManager()
            self._histories[session_id] = manager
        return manager

    def drop_session(self, session_id: str) -> None:
        """删除会话：工作记忆随之消失，长期记忆（情景/画像）保留。"""
        self._histories.pop((session_id or "").strip(), None)

    def add_assistant(self, session_id: str, content: str) -> None:
        """记录模型回复（供下一轮压缩使用）。"""
        content = (content or "").strip()
        if content:
            self.history(session_id).append(Message(content, "assistant"))

    # ---- 对外主入口 ----
    async def build(
        self,
        query: str,
        *,
        user_id: str,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """构建发给模型的消息列表。

        Raises:
            ContextOverflowError: 输入 token 超过 ``max_context_tokens``（预检，
                发生任何记忆读写之前直接失败）。
        """
        query = (query or "").strip()
        user_id = (user_id or "").strip()
        session_id = (session_id or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        if not user_id:
            raise ValueError("user_id 不能为空")
        if not session_id:
            raise ValueError("session_id 不能为空")

        history = self.history(session_id)

        # 1) 预算预检：超限直接失败，不做后续构建。
        input_tokens = self._input_tokens(history, query, system_prompt)
        if input_tokens > self.max_context_tokens:
            raise ContextOverflowError(
                f"输入约 {input_tokens} tokens，超过上下文窗口阈值 "
                f"{self.max_context_tokens}，已停止构建"
            )

        # 2) 工作记忆：追加本轮输入，必要时合并压缩。
        history.append(Message(query, "user"))
        if history.should_compress():
            await self._compress(session_id, user_id, history)

        # 3) 长期记忆：用户画像 + 相关历史会话摘要。
        profile_block = self._profile_block(user_id)
        episodes = self._recall(query, user_id, session_id)

        messages: list[dict[str, str]] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        if profile_block:
            messages.append({"role": "system", "content": profile_block})

        memory_block: dict[str, str] | None = None
        if episodes:
            memory_block = {
                "role": "system",
                "content": "## 相关历史记忆\n" + "\n".join(
                    episode.render() for episode in episodes
                ),
            }
            messages.append(memory_block)

        messages.extend(message.to_model_dict() for message in history.get_history())

        # 4) 组装后校验：先裁掉可选的检索记忆，仍超限才报错。
        tokens = self._messages_tokens(messages)
        if tokens > self.max_context_tokens and memory_block is not None:
            messages.remove(memory_block)
            tokens = self._messages_tokens(messages)
            logger.warning(
                "[Context] 检索记忆导致超限，已丢弃 %d 条历史记忆", len(episodes)
            )
        if tokens > self.max_context_tokens:
            raise ContextOverflowError(
                f"组装后约 {tokens} tokens，超过上下文窗口阈值 "
                f"{self.max_context_tokens}"
            )

        # 5) 画像更新异步执行，不阻塞本次请求。
        self._schedule_profile_update(user_id, query)

        logger.info(
            "[Context] build | session=%s user=%s history=%d memories=%d tokens=%d",
            session_id,
            user_id,
            len(history),
            len(episodes),
            tokens,
        )
        return messages

    async def aclose(self) -> None:
        """等待后台画像更新结束（测试/关停时调用）。"""
        if self._profile is not None:
            await self._profile.drain()

    # ---- 压缩 ----
    async def _compress(
        self,
        session_id: str,
        user_id: str,
        history: HistoryManager,
    ) -> bool:
        """把旧消息合并成摘要，写回同一会话的情景记忆，并裁剪工作记忆。"""
        archived = history.messages_to_archive()
        if not archived:
            history.enforce_limit()
            return False

        previous_summary = ""
        previous_count = 0
        try:
            episode = self.store.get(session_id)
            if episode is not None:
                previous_summary = episode.summary
                previous_count = episode.message_count
        except Exception:
            logger.exception("[Context] 读取已有情景记忆失败，按无摘要压缩")

        summary = await self._summarize(archived, previous_summary)
        if not summary:
            logger.warning("[Context] 摘要为空，仅按条数裁剪工作记忆")
            history.enforce_limit()
            return False

        try:
            self.store.upsert(
                session_id=session_id,
                user_id=user_id,
                summary=summary,
                message_count=previous_count + len(archived),
            )
        except Exception:
            logger.exception("[Context] 情景记忆写入失败，摘要仅保留在内存")

        compressed = history.compress(summary)
        logger.info(
            "[Context] compress | session=%s archived=%d kept=%d chars=%d",
            session_id,
            len(archived),
            KEEP_RECENT,
            len(summary),
        )
        return compressed

    async def _summarize(self, messages: Sequence[Message], previous: str) -> str:
        if self.summarizer is not None:
            try:
                result = self.summarizer(messages, previous)
                if inspect.isawaitable(result):
                    result = await result
                text = str(result or "").strip()
                if text:
                    return text[:SUMMARY_MAX_CHARS]
            except Exception:
                logger.exception("[Context] 自定义摘要器失败，退回默认摘要")

        return await self._default_summarize(messages, previous)

    async def _default_summarize(
        self, messages: Sequence[Message], previous: str
    ) -> str:
        """默认摘要器：优先 LLM 合并，模型不可用时退回本地有界摘要。"""
        dialog = "\n".join(message.to_text() for message in messages)
        prompt = SUMMARY_PROMPT.replace(
            "{previous}", previous.strip() or "（无）"
        ).replace("{dialog}", dialog)

        try:
            from agent.llm import build_llm

            llm = build_llm()
            reply = await llm.ainvoke(prompt)
            text = str(getattr(reply, "content", reply)).strip()
            if text:
                return text[:SUMMARY_MAX_CHARS]
        except Exception as exc:
            logger.warning("[Context] 摘要模型不可用，改用本地摘要：%s", exc)

        return _local_summary(messages, previous)

    # ---- 读取辅助 ----
    def _profile_block(self, user_id: str) -> str:
        try:
            return self.profile.render(user_id)
        except Exception:
            logger.exception("[Context] 读取用户画像失败，本次不注入画像")
            return ""

    def _recall(self, query: str, user_id: str, session_id: str) -> list[Any]:
        try:
            return self.store.search(
                query,
                user_id=user_id,
                top_k=self.top_k,
                exclude_session=session_id,
            )
        except Exception:
            logger.exception("[Context] 检索情景记忆失败，本次不注入历史记忆")
            return []

    def _schedule_profile_update(self, user_id: str, query: str) -> None:
        try:
            self.profile.schedule_update(user_id, query)
        except Exception:
            logger.exception("[Context] 调度用户画像更新失败")

    # ---- token 估算 ----
    def _input_tokens(
        self,
        history: HistoryManager,
        query: str,
        system_prompt: str | None,
    ) -> int:
        parts = [system_prompt or ""]
        parts.extend(message.to_text() for message in history.get_history())
        parts.append(f"[user] {query}")
        return estimate_tokens("\n".join(part for part in parts if part))

    @staticmethod
    def _messages_tokens(messages: Sequence[dict[str, str]]) -> int:
        # 每条消息约 4 token 的结构开销
        return sum(estimate_tokens(message["content"]) + 4 for message in messages)


__all__ = [
    "COMPRESS_AT",
    "CONTEXT_MAX_TOKENS",
    "ContextBuilder",
    "ContextOverflowError",
    "HISTORY_TOP_K",
    "KEEP_RECENT",
    "WORKING_MAX",
    "estimate_tokens",
]
