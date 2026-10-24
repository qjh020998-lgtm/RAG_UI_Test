"""DeepSeek Search API 封装：给 new_module 路由补外部业务流程知识。

为什么用 DeepSeek Search
------------------------
- 官方 Search API：``POST https://api.deepseek.com/v1/tools/search``
- 每 1000 次成功请求 5 元，失败/无结果/超时不计费
- 返回 ``summary``（600~800 字符）适合直接拼进 prompt

设计约束
--------
- 搜索是**补充信息**，失败必须降级为空字符串，不能阻塞生成
- 只在 ``new_module`` 路由触发（本地无代码/用例时才需要外部知识）
- 不缓存：搜索结果时效性强，且单次成本极低
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

DEFAULT_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
DEFAULT_COUNT = 5


class SearchUnavailableError(RuntimeError):
    """搜索不可用（未配置 key、网络失败等）。"""


@dataclass
class SearchResult:
    title: str
    url: str
    summary: str
    score: float = 0.0

    def format(self) -> str:
        lines = [f"- {self.title}"]
        if self.summary:
            lines.append(f"  {self.summary}")
        if self.url:
            lines.append(f"  来源: {self.url}")
        return "\n".join(lines)


def is_configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def search(
    query: str,
    count: int = DEFAULT_COUNT,
    *,
    api_key: str | None = None,
    url: str | None = None,
) -> list[SearchResult]:
    """调用 DeepSeek Search，返回结构化结果。"""
    key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    if not key:
        raise SearchUnavailableError("DEEPSEEK_API_KEY 未配置")

    endpoint = url or os.getenv("DEEPSEEK_SEARCH_URL", DEFAULT_SEARCH_URL)
    with httpx.Client(timeout=30) as client:
        response = client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"query": query[:128], "count": count, "need_content": False},
        )
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in data.get("results", []) or []:
        results.append(
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                summary=str(item.get("summary") or item.get("snippet") or ""),
                score=float(item.get("rerank_score") or 0.0),
            )
        )
    return results


def search_text(query: str, count: int = DEFAULT_COUNT) -> str:
    """搜索并拼成一段文本；任何失败都返回空字符串（降级）。"""
    try:
        results = search(query, count)
    except Exception as exc:  # noqa: BLE001 - 搜索失败不能阻塞生成
        log.warning("web 搜索失败，跳过：%s", exc)
        return ""

    # 只保留较优质结果，避免把低质量网页塞进 prompt
    good = [item for item in results if item.score >= 1.0 and item.summary]
    if not good:
        good = [item for item in results if item.summary]

    if not good:
        return ""
    return "\n\n".join(item.format() for item in good)
