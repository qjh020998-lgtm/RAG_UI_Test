"""测试场景意图标签、输出契约与纯规则识别。

四类意图分别对应四个 Agent；识别器不依赖 LLM、Embedding 或缓存。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class IntentCategory(Enum):
    """测试场景的四类意图。"""

    REQUIREMENT_DECOMPOSE = "requirement_decompose"  # 拆解需求文档 → GeneralAgent
    PAGE_CAPTURE = "page_capture"                    # 采集页面控件 → PageAgent
    CASE_GENERATION = "case_generation"              # 生成 Excel 测试用例 → CaseAgent
    CODE_GENERATION = "code_generation"              # 生成 UI 自动化代码 → CodeAgent


# 意图 → 目标 Agent 分组
_INTENT_GROUPS: dict[IntentCategory, str] = {
    IntentCategory.REQUIREMENT_DECOMPOSE: "general",
    IntentCategory.PAGE_CAPTURE: "page",
    IntentCategory.CASE_GENERATION: "case",
    IntentCategory.CODE_GENERATION: "code",
}


@dataclass
class IntentResult:
    """意图识别的输出契约（最小版）。"""

    intent: IntentCategory
    confidence: float
    intent_group: str = ""
    reasoning: str = ""
    latency_ms: float = 0.0
    source_scores: dict[str, float] = field(default_factory=dict)
    embedding_source: str = ""

    def __post_init__(self) -> None:
        if not self.intent_group:
            self.intent_group = _INTENT_GROUPS[self.intent]


# 预留：LLM few-shot / Embedding 匹配库；当前纯规则识别不依赖。
_TEMPLATES: dict[IntentCategory, list[str]] = {
    IntentCategory.REQUIREMENT_DECOMPOSE: [
        "拆解需求文档，输出测试点",
        "把这份 PRD 拆成功能点和验收标准",
        "分析需求文档里有哪些场景要覆盖",
    ],
    IntentCategory.PAGE_CAPTURE: [
        "采集页面控件，导出控件 JSON",
        "把新增弹窗的按钮和输入框抓下来",
        "登录 ERP 后截图并保存页面结构",
    ],
    IntentCategory.CASE_GENERATION: [
        "生成 Excel 测试用例",
        "给采购订单模块设计 20 条用例并导出 xlsx",
        "把刚采集的页面整理成测试用例",
    ],
    IntentCategory.CODE_GENERATION: [
        "生成 UI 自动化代码",
        "用 pytest + Playwright 写采购下单测试",
        "根据用例文件生成 Page Object 和测试代码",
    ],
}


# 关键词与权重：权重越高，证据越强。
_RULES: dict[IntentCategory, tuple[tuple[str, float], ...]] = {
    IntentCategory.REQUIREMENT_DECOMPOSE: (
        ("需求文档", 3.0),
        ("需求", 1.5),
        ("prd", 3.0),
        ("拆解", 2.0),
        ("验收标准", 2.0),
        ("功能点", 1.5),
        ("测试点", 1.0),
    ),
    IntentCategory.PAGE_CAPTURE: (
        ("采集", 2.5),
        ("控件", 2.5),
        ("页面结构", 2.0),
        ("页面", 1.0),
        ("弹窗", 1.5),
        ("按钮", 1.0),
        ("输入框", 1.0),
        ("截图", 1.5),
        ("dom", 1.5),
        ("浏览器", 1.0),
        ("登录", 1.0),
    ),
    IntentCategory.CASE_GENERATION: (
        ("生成测试用例", 4.0),
        ("测试用例", 3.0),
        ("用例", 2.0),
        ("生成用例", 3.0),
        ("用例设计", 2.0),
        ("测试场景", 2.0),
        ("测试设计", 2.0),
        ("excel", 2.0),
        ("xlsx", 2.0),
    ),
    IntentCategory.CODE_GENERATION: (
        ("自动化代码", 5.0),
        ("生成代码", 5.0),
        ("代码生成", 5.0),
        ("转成代码", 5.0),
        ("写代码", 4.0),
        ("编写代码", 4.0),
        ("写脚本", 3.5),
        ("编写脚本", 3.5),
        ("测试代码", 2.5),
        ("测试脚本", 2.5),
        ("pytest", 2.5),
        ("playwright", 2.5),
        ("page object", 2.5),
        ("ui 自动化", 2.5),
        ("代码", 1.5),
        ("脚本", 2.0),
        ("自动化", 1.5),
    ),
}


def _contains(text: str, keyword: str) -> bool:
    """中文按子串匹配，英文按 ASCII 单词边界匹配（兼容中文紧邻）。"""
    if keyword.isascii():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])"
        return re.search(pattern, text) is not None
    return keyword in text


def _keyword_scores(
    text: str,
) -> tuple[
    dict[IntentCategory, float],
    dict[IntentCategory, list[tuple[str, float]]],
]:
    scores = {intent: 0.0 for intent in IntentCategory}
    hits: dict[IntentCategory, list[tuple[str, float]]] = {
        intent: [] for intent in IntentCategory
    }
    for intent, rules in _RULES.items():
        for keyword, weight in rules:
            if _contains(text, keyword):
                scores[intent] += weight
                hits[intent].append((keyword, weight))
    return scores, hits


class IntentRecognizer:
    """测试场景意图识别器（纯规则）。"""

    async def recognize(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> IntentResult:
        """识别单条消息；``history`` 保留参数位，当前规则不使用。"""
        start = time.perf_counter()
        text = (message or "").strip().lower()
        if not text:
            raise ValueError("待识别文本不能为空")

        scores, hits = _keyword_scores(text)
        total = sum(scores.values())
        if total <= 0:
            raise ValueError(
                "未识别到测试场景意图，请补充需求、控件、用例或代码等关键词"
            )

        intent = max(scores, key=lambda item: scores[item])
        top = scores[intent]
        confidence = round(min(0.99, (top / total) * min(1.0, top / 4.0)), 3)
        detail = "、".join(
            f"{keyword}({weight:g})"
            for keyword, weight in sorted(hits[intent], key=lambda item: -item[1])
        )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            reasoning=f"关键词命中：{detail}",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            source_scores={"pattern": confidence},
        )


__all__ = ["IntentCategory", "IntentRecognizer", "IntentResult"]
