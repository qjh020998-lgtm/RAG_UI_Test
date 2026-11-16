"""
用户画像模块，用于生成和管理用户画像，提供用户信息的查询和更新功能。

- 存储：ChromaDB ``user_profile`` collection，文档 id 固定为 ``user_id``，
  同一用户反复更新只会覆盖同一条记录（稳定存储）。
- 更新：``schedule_update`` 把抽取任务丢到后台事件循环，不阻塞主请求；
  ``drain`` 可在测试或关停时等待后台任务结束。
- 抽取：默认用 LLM 从用户消息里提取稳定偏好/关键实体，失败时跳过本次更新；
  也可以注入 ``extractor`` 做规则抽取或测试替身。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from utils.logger import get_logger

from .store import DEFAULT_DB_DIR, Embedder, hash_embedding

logger = get_logger(__name__)

PROFILE_COLLECTION = "user_profile"

# 抽取器：输入 (用户消息, 已有画像)，返回 {标签: 描述}；同步/异步均可。
Extractor = Callable[[str, Mapping[str, str]], Mapping[str, str] | Awaitable[Mapping[str, str]]]

EXTRACT_PROMPT = """从用户消息中提取值得长期记住的稳定偏好和关键实体（常用模块/页面、ERP 操作习惯、语言、角色、常用路径等）。
只提取长期有效的信息；一次性任务、临时指令、寒暄、客套话不要提取。
已有画像（供参考，不要重复）：
{existing}
用户消息：
{text}

只输出一个 JSON 对象，键是简短中文标签，值是简短中文描述，例如：
{"语言偏好": "中文", "常用模块": "采购订单"}
没有可提取内容时输出 {}。不要输出 JSON 以外的任何内容。"""

MAX_FACTS = 20
MAX_KEY_CHARS = 20
MAX_VALUE_CHARS = 200
MAX_INPUT_CHARS = 2000


def _parse_facts(raw: str) -> dict[str, str]:
    """从模型输出里解析 JSON 对象，容忍代码块包裹和多余文字。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    facts: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            value = "、".join(str(item) for item in value if str(item).strip())
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            facts[str(key).strip()] = str(value).strip()
    return facts


class UserProfile:
    """用户画像：按 ``user_id`` 稳定存储，异步更新。"""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        collection_name: str = PROFILE_COLLECTION,
        embedder: Embedder | None = None,
        client: Any = None,
        extractor: Extractor | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_DIR
        self.collection_name = collection_name
        self.embedder = embedder or hash_embedding
        self.extractor = extractor or self._llm_extract
        self._client = client
        self._collection: Any = None
        self._pending: set[asyncio.Task[Any]] = set()
        self._write_lock: asyncio.Lock | None = None

    # ---- 基础设施 ----
    def _get_collection(self) -> Any:
        """延迟创建 ChromaDB client / collection。"""
        if self._collection is None:
            if self._client is None:
                import chromadb
                from chromadb.config import Settings

                self.path.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.path),
                    settings=Settings(anonymized_telemetry=False),
                )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                configuration={"hnsw": {"space": "cosine"}},
            )
        return self._collection

    def _get_lock(self) -> asyncio.Lock:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    # ---- 读取 ----
    def get(self, user_id: str) -> dict[str, str]:
        """读取画像；不存在时返回空字典。"""
        user_id = (user_id or "").strip()
        if not user_id:
            return {}

        result = self._get_collection().get(
            ids=[user_id],
            include=["metadatas"],
        )
        metadatas = result.get("metadatas") or []
        if not metadatas:
            return {}

        raw = (metadatas[0] or {}).get("facts") or "{}"
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if str(key).strip() and str(value).strip()
        }

    def render(self, user_id: str) -> str:
        """渲染成可直接拼进 system prompt 的文本块。"""
        return self._render_facts(self.get(user_id))

    @staticmethod
    def _render_facts(facts: Mapping[str, str]) -> str:
        if not facts:
            return ""
        lines = "\n".join(f"- {key}：{value}" for key, value in facts.items())
        return f"## 用户画像\n{lines}"

    # ---- 写入 ----
    def upsert(self, user_id: str, facts: Mapping[str, str]) -> dict[str, str]:
        """按 key 合并画像（同 key 覆盖），返回合并后的完整画像。"""
        user_id = (user_id or "").strip()
        if not user_id:
            raise ValueError("user_id 不能为空")

        cleaned = self._clean_facts(facts)
        if not cleaned:
            return self.get(user_id)

        merged = {**self.get(user_id), **cleaned}
        self._store(user_id, merged)
        return merged

    def _store(self, user_id: str, facts: Mapping[str, str]) -> None:
        rendered = self._render_facts(facts)
        metadata = {
            "user_id": user_id,
            "facts": json.dumps(dict(facts), ensure_ascii=False),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._get_collection().upsert(
            ids=[user_id],
            documents=[rendered],
            embeddings=[[float(value) for value in self.embedder(rendered)]],
            metadatas=[metadata],
        )

    def delete(self, user_id: str) -> None:
        """删除用户画像。"""
        user_id = (user_id or "").strip()
        if user_id:
            self._get_collection().delete(ids=[user_id])

    # ---- 异步更新（不阻塞主请求） ----
    def schedule_update(self, user_id: str, text: str) -> asyncio.Task[Any] | None:
        """把画像抽取丢到后台任务，立即返回。

        没有运行中的事件循环时只记录告警并跳过（不阻塞调用方）。
        """
        user_id = (user_id or "").strip()
        text = (text or "").strip()
        if not user_id or not text:
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[Profile] 没有运行中的事件循环，跳过异步画像更新")
            return None

        task = loop.create_task(self._background_update(user_id, text))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def _background_update(self, user_id: str, text: str) -> dict[str, str]:
        try:
            return await self.aupdate(user_id, text)
        except Exception:
            logger.exception("[Profile] 异步更新用户画像失败：user_id=%s", user_id)
            return {}

    async def aupdate(self, user_id: str, text: str) -> dict[str, str]:
        """抽取并合并画像（异步版本）。"""
        user_id = (user_id or "").strip()
        text = (text or "").strip()
        if not user_id or not text:
            return self.get(user_id) if user_id else {}

        existing = self.get(user_id)
        facts = await self._extract(text, existing)
        if not facts:
            return existing

        async with self._get_lock():
            return self.upsert(user_id, facts)

    async def drain(self, timeout: float | None = None) -> None:
        """等待所有后台画像更新结束（测试/关停用）。"""
        pending = [task for task in self._pending if not task.done()]
        if pending:
            _done, not_done = await asyncio.wait(pending, timeout=timeout)
            for task in not_done:
                logger.warning("[Profile] 画像更新仍在执行：%s", task)
        for task in list(self._pending):
            if task.done():
                self._pending.discard(task)

    # ---- 抽取 ----
    async def _extract(self, text: str, existing: Mapping[str, str]) -> dict[str, str]:
        try:
            result = self.extractor(text, existing)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("[Profile] 画像抽取失败，跳过本次更新")
            return {}
        if not isinstance(result, dict):
            return {}
        return self._clean_facts(result)

    async def _llm_extract(self, text: str, existing: Mapping[str, str]) -> dict[str, str]:
        """默认抽取器：用项目统一的 ChatOpenAI 模型输出 JSON。"""
        try:
            from agent.llm import build_llm

            llm = build_llm()
            prompt = EXTRACT_PROMPT.replace(
                "{existing}", json.dumps(dict(existing), ensure_ascii=False)
            ).replace("{text}", text[:MAX_INPUT_CHARS])
            reply = await llm.ainvoke(prompt)
            return _parse_facts(str(getattr(reply, "content", reply)))
        except Exception as exc:
            logger.warning("[Profile] 画像抽取模型不可用，跳过本次更新：%s", exc)
            return {}

    @staticmethod
    def _clean_facts(facts: Mapping[str, Any]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in dict(facts).items():
            key = str(key).strip()[:MAX_KEY_CHARS]
            value = str(value).strip()[:MAX_VALUE_CHARS]
            if key and value:
                cleaned[key] = value
            if len(cleaned) >= MAX_FACTS:
                break
        return cleaned


__all__ = ["PROFILE_COLLECTION", "UserProfile"]
