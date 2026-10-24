"""资料收集器：从三个知识库找资料，打包成一份"参考手册"。

设计思想（借鉴 all_in_rag 的简化版）
------------------------------------
1. 把用户的功能描述转成向量
2. 先从 **code 库** 找涉及页面的方法（精确按 page 过滤 + 语义兜底）
3. 从 **test 库** 找写法相似的已有测试
4. 从 **knowledge 库** 找业务规则
5. 做一次"方法存在性"检查，把可疑调用列出来
6. 拼成一段文本，交给 LLM 生成测试代码

刻意保持简单
------------
- 不做 RRF 融合：三层分开检索、分开给，LLM 自己看
- 不做重排：dense cosine 已经是可比的
- 不做增量：索引由 indexer 离线建好，这里只读
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import Sequence

from agent.embedding import get_embedder
from agent.milvus_connect import MilvusStore, get_store

# 层 -> collection 名（与 .env 的默认值一致）
COLLECTIONS: dict[str, str] = {
    "code": "erp_code",
    "test": "erp_test",
    "knowledge": "erp_knowledge",
}

# 默认每层取几条；页面方法最多 8 条，避免 prompt 爆掉
DEFAULT_LIMITS: dict[str, int] = {"code": 8, "test": 5, "knowledge": 4}

# 各层检索时需要的字段
OUTPUT_FIELDS: dict[str, list[str]] = {
    "code": ["chunk_id", "file", "name", "class_name", "page", "start_line", "content"],
    "test": ["chunk_id", "file", "case_id", "name", "module", "sheet", "content"],
    "knowledge": ["chunk_id", "file", "module", "page", "content"],
}


@dataclass
class Snippet:
    """一条检索结果。"""

    content: str
    source: str
    score: float
    layer: str = ""
    name: str = ""

    def format(self, max_chars: int = 1200) -> str:
        text = self.content
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...（截断 {len(text) - max_chars} 字符）"
        return f"来源: {self.source}\n{text}"


@dataclass
class RetrievalResult:
    """一次收集的全部成果。"""

    page_methods: list[Snippet] = field(default_factory=list)
    similar_tests: list[Snippet] = field(default_factory=list)
    business_knowledge: list[Snippet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_manual(self) -> str:
        """把所有资料拼成一段文字，给 AI 看。"""
        blocks: list[str] = []

        if self.page_methods:
            blocks.append(
                "## 可用的页面方法（只能用这些）\n"
                + "\n\n".join(snippet.format() for snippet in self.page_methods)
            )
        if self.similar_tests:
            blocks.append(
                "## 已有的类似测试（参考这些写法）\n"
                + "\n\n".join(snippet.format() for snippet in self.similar_tests)
            )
        if self.business_knowledge:
            blocks.append(
                "## 业务规则\n"
                + "\n\n".join(snippet.format(max_chars=800) for snippet in self.business_knowledge)
            )
        if self.warnings:
            blocks.append("## 注意\n" + "\n".join(f"- {w}" for w in self.warnings))

        return "\n\n".join(blocks)


class Retriever:
    """资料收集器：查询 → 三层检索 → 参考手册。"""

    def __init__(self, store: MilvusStore | None = None, embedder=None):
        self.store = store or get_store()
        self.embedder = embedder or get_embedder()

    def collect(
        self,
        query: str,
        pages: Sequence[str] | None = None,
        *,
        module: str = "",
        sheet: str = "",
        limits: dict[str, int] | None = None,
    ) -> RetrievalResult:
        """主入口。

        Args:
            query: 功能描述，如 "按库存上限保存请购单"
            pages: 涉及页面（`code.page` 的值），如 ["采购管理"]
            module: 业务模块，如 "请购单"（用于 test/knowledge 过滤）
            sheet: Excel 工作表，如 "采购页"（用于 test 过滤）
            limits: 覆盖默认配额
        """
        pages = list(pages or [])
        limits = {**DEFAULT_LIMITS, **(limits or {})}
        dense, _sparse = self.embedder.encode_query(query)
        result = RetrievalResult()

        # 1. code：按页面过滤做语义检索（页面方法太多，不做全量精确注入）
        result.page_methods = self._search(
            "code", dense, limits["code"], self._code_expr(pages)
        )

        # 2. test：找写法相似的用例
        result.similar_tests = self._search(
            "test", dense, limits["test"], self._test_expr(sheet)
        )

        # 3. knowledge：找业务规则
        result.business_knowledge = self._search(
            "knowledge", dense, limits["knowledge"], self._knowledge_expr(module)
        )

        # 4. 方法存在性检查：测试里调用、但 code 层没有的方法
        result.warnings = self._check_methods(result)

        return result

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def _search(self, layer: str, dense: list[float], limit: int, expr: str) -> list[Snippet]:
        if limit <= 0:
            return []
        hits = self.store.search(
            COLLECTIONS[layer],
            [dense],
            limit=limit,
            expr=expr,
            output_fields=OUTPUT_FIELDS[layer],
        )
        return [self._to_snippet(layer, hit) for hit in (hits[0] if hits else [])]

    @staticmethod
    def _to_snippet(layer: str, hit) -> Snippet:
        entity = hit.entity
        content = entity.get("content") or ""
        file = entity.get("file") or ""
        if layer == "code":
            name = entity.get("name") or ""
            source = f"{file}#{name}" if name else file
        elif layer == "test":
            case_id = entity.get("case_id") or ""
            name = entity.get("name") or ""
            source = f"{file}#{case_id or name}"
        else:
            page = entity.get("page") or ""
            source = f"{file} p{page}" if page not in ("", None) else file
        return Snippet(
            content=content,
            source=source,
            score=float(getattr(hit, "distance", 0.0)),
            layer=layer,
            name=str(entity.get("name") or ""),
        )

    @staticmethod
    def _code_expr(pages: Sequence[str]) -> str:
        # method 给调用方式，class_const 给 locator（控件名的来源）
        parts = ['layer == "code"', 'kind in ["method", "class_const"]']
        if pages:
            joined = ",".join(f'"{page}"' for page in pages)
            parts.append(f"page in [{joined}]")
        return " and ".join(parts)

    @staticmethod
    def _test_expr(sheet: str) -> str:
        parts = ['layer == "test"']
        if sheet:
            parts.append(f'sheet == "{sheet}"')
        return " and ".join(parts)

    @staticmethod
    def _knowledge_expr(module: str) -> str:
        parts = ['layer == "knowledge"']
        if module:
            parts.append(f'module == "{module}"')
        return " and ".join(parts)

    # ------------------------------------------------------------------
    # 方法存在性检查
    # ------------------------------------------------------------------

    _CALL_RE = re.compile(r"\bpage\.([A-Za-z_]\w*)\s*\(")

    # Playwright Page 的原生方法：不是 Page Object 的契约，检查时忽略
    _PLAYWRIGHT_METHODS = frozenset(
        {
            "wait_for_timeout",
            "wait_for_selector",
            "wait_for_load_state",
            "wait_for_url",
            "wait_for_event",
            "locator",
            "get_by_role",
            "get_by_text",
            "get_by_label",
            "get_by_placeholder",
            "get_by_test_id",
            "evaluate",
            "screenshot",
            "goto",
            "reload",
            "go_back",
            "go_forward",
            "set_default_timeout",
            "expect",
            "pause",
        }
    )

    def _check_methods(self, result: RetrievalResult) -> list[str]:
        """把测试里调用、但 code 层没有的方法列为警告。

        这是最轻量的"矛盾检测"：不比较两个测试的写法，只检查
        "测试调用的方法是否真的存在于 Page 类里"——幻觉的主要来源。
        """
        called: set[str] = set()
        for snippet in result.similar_tests:
            called.update(self._CALL_RE.findall(snippet.content))
        called.discard("page")  # page.page.xxx 的自引用
        called.difference_update(self._PLAYWRIGHT_METHODS)
        if not called:
            return []

        # 查全库方法名（不限页面），避免"检索没覆盖到就误报"
        rows = self.store.query(
            COLLECTIONS["code"],
            'layer == "code" and kind == "method"',
            output_fields=["name"],
            limit=500,
        )
        known = {row.get("name", "") for row in rows if row.get("name")}
        known.update(snippet.name for snippet in result.page_methods if snippet.name)

        missing = sorted(name for name in called if name not in known)
        if not missing:
            return []
        return [f"测试里调用了 code 库中未检索到的方法，请确认是否存在：{', '.join(missing)}"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="从三层知识库收集参考资料")
    parser.add_argument("--query", required=True, help="功能描述")
    parser.add_argument("--pages", default="", help="涉及页面，逗号分隔，如 采购管理")
    parser.add_argument("--module", default="", help="业务模块，如 请购单")
    parser.add_argument("--sheet", default="", help="Excel 工作表，如 采购页")
    parser.add_argument("--limit", type=int, default=0, help="覆盖 code 层配额")
    args = parser.parse_args(argv)

    pages = [part.strip() for part in args.pages.split(",") if part.strip()]
    limits = {"code": args.limit} if args.limit else None

    retriever = Retriever()
    result = retriever.collect(
        args.query,
        pages,
        module=args.module,
        sheet=args.sheet,
        limits=limits,
    )
    print(result.to_manual())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
