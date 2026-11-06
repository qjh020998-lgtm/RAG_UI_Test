"""
web API 工具自测：web_fetch / web_search。

输出目录：
    testself/selfcache/web_json/
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from DomToExcel.tools.context import ToolContext
from DomToExcel.tools.web import CloseBrowserTool, WebFetchTool, WebSearchTool

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "selfcache" / "web_json"
BASE_URL = "http://39.101.122.147:3000"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(tool, **kwargs) -> str:
    return asyncio.run(tool.execute(**kwargs))


@pytest.fixture(scope="module")
def ctx() -> ToolContext:
    OUT.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        config={"headless": True},
        workspace=ROOT,
        run_id="web_self_test",
        artifacts=OUT,
        base_url=BASE_URL,
    )


def test_web_fetch_real(ctx: ToolContext) -> None:
    raw = _run(WebFetchTool(ctx), url="https://example.com", max_chars=1000)
    (OUT / "web_fetch_raw.txt").write_text(raw, encoding="utf-8")

    assert not raw.startswith("错误："), raw
    data = json.loads(raw)
    assert data["status"] == 200
    assert "Example Domain" in data["content"]
    assert data["truncated"] is False
    _write_json(OUT / "web_fetch.json", data)


def test_web_search_real(ctx: ToolContext) -> None:
    raw = _run(WebSearchTool(ctx), query="jshERP 采购订单", max_results=3)
    (OUT / "web_search_raw.txt").write_text(raw, encoding="utf-8")

    if "缺少 DEEPSEEK_API_KEY" in raw:
        pytest.skip("DEEPSEEK_API_KEY 为空，web_search 无法真实调用；已保留 raw 输出")
    assert not raw.startswith("错误："), raw
    data = json.loads(raw)
    assert isinstance(data.get("results"), list)
    assert data["results"]
    _write_json(OUT / "web_search.json", data)


def test_web_fetch_invalid_scheme(ctx: ToolContext) -> None:
    raw = _run(WebFetchTool(ctx), url="ftp://example.com/file.txt")
    (OUT / "web_fetch_invalid_raw.txt").write_text(raw, encoding="utf-8")
    assert "只支持 http/https" in raw


def test_web_fetch_truncate(ctx: ToolContext) -> None:
    raw = _run(WebFetchTool(ctx), url="https://example.com", max_chars=20)
    data = json.loads(raw)
    assert data["truncated"] is True
    assert len(data["content"]) <= 20


def test_close_browser_without_session() -> None:
    ctx = ToolContext(
        config={"headless": True},
        workspace=ROOT,
        run_id="close_browser_test",
        artifacts=OUT,
    )
    first = _run(CloseBrowserTool(ctx))
    second = _run(CloseBrowserTool(ctx))
    assert "没有需要关闭的浏览器" in first
    assert "没有需要关闭的浏览器" in second
