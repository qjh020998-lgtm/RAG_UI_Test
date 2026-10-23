"""三层 chunk 切分：CodeChunk / TestChunk / KnowledgeChunk。

职责边界
--------
- **只做切分与组装**：把 ``tools/ast_parser.py`` 的 AST 结果、
  ``tools/document`` 的文档结果，切成可嵌入的 chunk
- 不做 AST 解析（在 ``tools/ast_parser.py``）
- 不做嵌入、不写向量库（P3.2 的 indexer 负责）

metadata 设计（2026-10-23 精简）
-------------------------------
只保留**身份键**与**过滤键**，长文本一律只进 ``text``：

- 身份键：``layer / kind / file / class / name / qualname / case_id``
- 过滤键：``module / priority / automation_status / page / markers / fixtures / scope``
- 定位键：``start_line / end_line / chunk_index``
- 去重键：``content_hash``

``signature / docstring / decorators / params / depends_on / assert_messages``
等字段不再进 metadata——它们已经在 ``text`` 的 header 或源码里。
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agent.models import TestCase
from agent.parser.excel_reader import read_workbook
from tools.ast_parser import (
    ParsedClass,
    ParsedConstant,
    ParsedFixture,
    ParsedMethod,
    ParsedTestCase,
    parse_conftest,
    parse_module,
    parse_test_file,
)
from tools.document import DocumentProcessor
from tools.document.schema import DocumentChunk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"
DOCS_DIR = PROJECT_ROOT / "docs"
TEST_DIR = PROJECT_ROOT / "testcases"
EXCEL_PATH = DOCS_DIR / "cases" / "ERP系统测试用例.xlsx"

# code 层要索引的路径（相对项目根）
CODE_TARGETS: tuple[str, ...] = (
    "page",
    "pw_core",
    "data_loader.py",
    "utils",
)

EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".git",
        ".pytest_cache",
        "testself",       # 框架自测，不是业务范式
        "agent",          # Stage 2 自身，避免自我引用
        "tools",          # 文档/AST 工具，不是测试 API
        "testcases",      # 归 test 层
        "volumes",
        "cache",
        "knowledge-store",
        "artifacts",
        "reports",
    }
)

# POM 文件 -> 业务页面名（用于 metadata["page"]）
PAGE_NAMES: dict[str, str] = {
    "login_page.py": "登录",
    "purchase_page.py": "采购管理",
    "retail_page.py": "零售管理",
}

# Excel 用例编号：采购-001 / 零售-032
_CASE_ID_RE = re.compile(r"^(采购|零售)-\d{3}$")


# ---------------------------------------------------------------------------
# chunk 定义：保持最小
# ---------------------------------------------------------------------------


@dataclass
class BaseChunk:
    """一个可嵌入的语义单元。"""

    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class CodeChunk(BaseChunk):
    """框架代码块。"""


@dataclass
class TestChunk(BaseChunk):
    """测试用例块。"""


@dataclass
class KnowledgeChunk(BaseChunk):
    """业务知识块。"""


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _slug(text: str, limit: int = 24) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return value[:limit] or "x"


def _h4(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:4]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _first_line(text: str, limit: int = 100) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:limit]


def _chunk_id(rel_path: str, kind: str, qualname: str) -> str:
    """ASCII 标识型主键，≤64 字符。

    ``{mod_slug}_{kind}_{name}_{hash4}``
    - ``mod_slug`` 用文件 stem（``page/purchase_page.py`` -> ``purchase_page``）
    - 行号不参与主键：插一行不会让后续主键漂移
    - hash4 取自 ``file::qualname``，只用于消除同名冲突
    """
    stem = Path(rel_path).stem if rel_path.endswith(".py") else rel_path
    mod = _slug(stem, 20)
    name = _slug(qualname.split(".")[-1], 28)
    return f"{mod}_{kind}_{name}_{_h4(f'{rel_path}::{qualname}')}"


def _iter_code_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for target in CODE_TARGETS:
        path = root / target
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            for item in sorted(path.rglob("*.py")):
                if any(part in EXCLUDE_DIRS for part in item.parts):
                    continue
                files.append(item)
    return sorted(set(files))


def _case_id_of(test: ParsedTestCase) -> str:
    """从 ``@allure.story("采购-008 ...")`` 取用例编号。"""
    story = (test.allure or {}).get("story", "")
    if not story:
        return ""
    token = story.split(" ")[0].strip()
    return token if _CASE_ID_RE.match(token) else ""


def _load_case_specs(excel_path: Path | None) -> dict[str, tuple[str, TestCase]]:
    """读 Excel 用例表，返回 ``{case_id: (sheet, TestCase)}``；失败时退化为空表。

    ``sheet`` 取工作表名（采购页 / 零售页 / 以后新增的 sheet），写进 chunk metadata；
    新增工作表时 ``read_workbook`` 自动发现，不需要改代码。
    """
    if not excel_path or not Path(excel_path).exists():
        return {}
    try:
        specs = read_workbook(excel_path)
    except Exception as exc:  # 表头变化/文件损坏时不能阻断切分
        print(f"[WARN] Excel 读取失败，退化为纯代码切分：{exc}", file=sys.stderr)
        return {}
    return {
        case.id: (spec.sheet or "", case)
        for spec in specs.values()
        for case in spec.test_cases
    }


# ---------------------------------------------------------------------------
# CodeChunk
# ---------------------------------------------------------------------------


def _page_of(rel_path: str) -> str:
    return PAGE_NAMES.get(Path(rel_path).name, "")


def _code_meta(rel_path: str, kind: str, name: str, qualname: str) -> dict:
    """code 层的身份键 + 过滤键。"""
    return {
        "layer": "code",
        "kind": kind,
        "file": rel_path,
        "class": "",
        "name": name,
        "qualname": qualname,
        "module": "",
        "priority": "",
        "automation_status": "",
        "case_id": "",
        "page": _page_of(rel_path),
        "sheet": "",
        "markers": [],
        "fixtures": [],
        "scope": "",
        "chunk_index": -1,
        "is_plugin": False,
        "content_hash": "",
        "start_line": 0,
        "end_line": 0,
    }


def _method_chunk(method: ParsedMethod, cls: ParsedClass, rel_path: str) -> CodeChunk:
    header = [f"# File: {rel_path}", f"# Class: {cls.name}"]
    if cls.docstring:
        header[1] += f" — {_first_line(cls.docstring)}"
    header.append(f"# Signature: {method.signature}")
    if method.decorators:
        header.append(f"# Decorators: {', '.join(method.decorators)}")
    if method.is_property:
        header.append("# Note: @property，调用时不要加括号")

    meta = _code_meta(rel_path, "method", method.name, method.qualname)
    meta.update(
        {
            "class": cls.name,
            "start_line": method.start_line,
            "end_line": method.end_line,
        }
    )
    return CodeChunk(
        chunk_id=_chunk_id(rel_path, "mth", method.qualname),
        text="\n".join(header) + "\n" + method.source,
        metadata=meta,
    )


def _class_chunk(cls: ParsedClass, rel_path: str) -> CodeChunk:
    method_names = [m.name for m in cls.public_methods]
    header = [
        f"# File: {rel_path}",
        f"# Class: {cls.name}" + (f" — {_first_line(cls.docstring)}" if cls.docstring else ""),
    ]
    if cls.bases:
        header.append(f"# Bases: {', '.join(cls.bases)}")
    if method_names:
        header.append(f"# Public methods: {', '.join(method_names)}")

    meta = _code_meta(rel_path, "class", cls.name, cls.name)
    meta.update(
        {
            "class": cls.name,
            "start_line": cls.start_line,
            "end_line": cls.end_line,
        }
    )
    return CodeChunk(
        chunk_id=_chunk_id(rel_path, "cls", cls.name),
        text="\n".join(header) + "\n\n" + (cls.docstring or f"{cls.name} 类。"),
        metadata=meta,
    )


def _const_chunk(block: list[ParsedConstant], cls: ParsedClass, rel_path: str) -> CodeChunk:
    names = [c.name for c in block]
    source = "\n".join(c.source for c in block)
    header = [
        f"# File: {rel_path}",
        f"# Class: {cls.name} — locators / constants",
        f"# Names: {', '.join(names)}",
    ]

    meta = _code_meta(rel_path, "class_const", names[0], f"{cls.name}::{names[0]}")
    meta.update(
        {
            "class": cls.name,
            "start_line": block[0].start_line,
            "end_line": block[-1].end_line,
        }
    )
    return CodeChunk(
        chunk_id=_chunk_id(rel_path, "cst", f"{cls.name}::{names[0]}"),
        text="\n".join(header) + "\n" + source,
        metadata=meta,
    )


def _function_chunk(function: ParsedMethod, rel_path: str) -> CodeChunk:
    header = [f"# File: {rel_path}", f"# Signature: {function.signature}"]
    if function.decorators:
        header.append(f"# Decorators: {', '.join(function.decorators)}")

    meta = _code_meta(rel_path, "module_function", function.name, function.qualname)
    meta.update(
        {
            "start_line": function.start_line,
            "end_line": function.end_line,
        }
    )
    return CodeChunk(
        chunk_id=_chunk_id(rel_path, "fn", function.qualname),
        text="\n".join(header) + "\n" + function.source,
        metadata=meta,
    )


def _fixture_chunk(fixture: ParsedFixture, rel_path: str) -> CodeChunk:
    header = [
        f"# File: {rel_path}",
        f"# Fixture: {fixture.name}",
        f"# Scope: {fixture.scope}" + (" (autouse)" if fixture.autouse else ""),
    ]
    if fixture.params:
        header.append(f"# Params: {', '.join(fixture.params)}")
    if fixture.depends_on:
        header.append(f"# Depends on: {', '.join(fixture.depends_on)}")
    if fixture.docstring:
        header.append(f"# Doc: {_first_line(fixture.docstring)}")

    meta = _code_meta(rel_path, "fixture", fixture.name, fixture.name)
    meta.update(
        {
            "scope": fixture.scope,
            "start_line": fixture.start_line,
            "end_line": fixture.end_line,
        }
    )
    return CodeChunk(
        chunk_id=_chunk_id(rel_path, "fx", fixture.name),
        text="\n".join(header) + "\n" + fixture.source,
        metadata=meta,
    )


def chunk_code(root: str | Path = PROJECT_ROOT) -> list[CodeChunk]:
    """切分框架代码：Page 方法、类、类常量、fixture、模块级函数。"""
    project_root = Path(root)
    chunks: list[CodeChunk] = []

    for path in _iter_code_files(project_root):
        rel_path = _rel(path, project_root)
        module = parse_module(path)

        for cls in module.classes:
            chunks.append(_class_chunk(cls, rel_path))
            for block in cls.constant_blocks:
                chunks.append(_const_chunk(block, cls, rel_path))
            for method in cls.methods:
                if method.is_public:
                    chunks.append(_method_chunk(method, cls, rel_path))

        for function in module.functions:
            if function.is_public:
                chunks.append(_function_chunk(function, rel_path))

    conftest = project_root / "conftest.py"
    if conftest.exists():
        rel_path = _rel(conftest, project_root)
        for fixture in parse_conftest(conftest):
            chunks.append(_fixture_chunk(fixture, rel_path))

    return chunks


# ---------------------------------------------------------------------------
# TestChunk
# ---------------------------------------------------------------------------


def _test_meta(rel_path: str, kind: str, name: str, qualname: str) -> dict:
    return {
        "layer": "test",
        "kind": kind,
        "file": rel_path,
        "class": "",
        "name": name,
        "qualname": qualname,
        "module": "",
        "priority": "",
        "automation_status": "",
        "case_id": "",
        "page": "",
        "sheet": "",
        "markers": [],
        "fixtures": [],
        "scope": "",
        "chunk_index": -1,
        "is_plugin": False,
        "content_hash": "",
        "start_line": 0,
        "end_line": 0,
    }


def _truncate(text: str, limit: int) -> str:
    value = (text or "").strip().replace("\n", " ")
    return value if len(value) <= limit else value[:limit] + "…"


def _join_steps(steps: list[str], *, max_steps: int = 8, step_limit: int = 60) -> str:
    parts = [_truncate(step, step_limit) for step in steps[:max_steps]]
    return "；".join(part for part in parts if part)


def _semantic_header(test: ParsedTestCase, case: TestCase) -> list[str]:
    """把 Excel 的用例语义拼成 ``# key: value`` 行；空值跳过。"""
    story = (test.allure or {}).get("story", "").strip()
    title = story[len(case.id) :].strip() if story.startswith(case.id) else story
    lines = [f"# Case: {case.id}" + (f" {title}" if title else "")]

    priority = getattr(case.priority, "value", "") or ""
    if priority:
        lines.append(f"# Priority: {priority}")
    if case.module:
        lines.append(f"# Module: {_truncate(case.module, 60)}")
    if case.purpose:
        lines.append(f"# Purpose: {_truncate(case.purpose, 120)}")

    preconditions = "；".join(
        _truncate(item, 100) for item in case.preconditions[:2] if item
    )
    if preconditions:
        lines.append(f"# Precondition: {preconditions}")

    steps = _join_steps(case.steps)
    if steps:
        lines.append(f"# Steps: {steps}")

    if case.expected_result:
        lines.append(f"# Expected: {_truncate(case.expected_result, 200)}")
    if case.assertions:
        lines.append(f"# Assertion: {_truncate(case.assertions, 300)}")
    if case.automation_status:
        lines.append(f"# Status: {case.automation_status}")
    return lines


def _test_case_chunk(
    test: ParsedTestCase,
    rel_path: str,
    case_entry: tuple[str, TestCase] | None,
) -> TestChunk:
    sheet, case = case_entry if case_entry else ("", None)
    header = [f"# File: {rel_path}"]

    if case is not None:
        # 有 Excel 映射：语义 header + 代码身份
        header.extend(_semantic_header(test, case))
        if test.class_name:
            header.append(f"# Class: {test.class_name}")
        header.append(f"# Test: {test.name}")
    else:
        # 无 Excel 映射：保持原有 header，不因注入逻辑丢字段
        if test.class_name:
            header.append(f"# Class: {test.class_name}")
        header.append(f"# Test: {test.name}")
        if test.allure.get("story"):
            header.append(f"# Story: {test.allure['story']}")

    if test.markers:
        header.append(f"# Markers: {', '.join(test.markers)}")
    if test.fixtures:
        header.append(f"# Fixtures: {', '.join(test.fixtures)}")
    for param in test.parametrize:
        header.append(f"# Parametrize: {param['argnames']}")

    meta = _test_meta(rel_path, "test_case", test.name, test.qualname)
    meta.update(
        {
            "class": test.class_name,
            "case_id": _case_id_of(test) or (case.id if case else ""),
            "module": case.module if case else "",
            "priority": getattr(case.priority, "value", "") if case else "",
            "automation_status": case.automation_status if case else "",
            "sheet": sheet,
            "markers": list(test.markers),
            "fixtures": list(test.fixtures),
            "start_line": test.start_line,
            "end_line": test.end_line,
        }
    )
    return TestChunk(
        chunk_id=_chunk_id(rel_path, "tst", test.qualname),
        text="\n".join(header) + "\n" + test.source,
        metadata=meta,
    )


def _test_class_chunk(cls, rel_path: str) -> TestChunk:
    names = [t.name for t in cls.tests]
    header = [
        f"# File: {rel_path}",
        f"# Class: {cls.name}" + (f" — {_first_line(cls.docstring)}" if cls.docstring else ""),
        f"# Tests ({len(names)}): {', '.join(names[:20])}",
    ]

    meta = _test_meta(rel_path, "test_class", cls.name, cls.name)
    meta.update(
        {
            "class": cls.name,
            "markers": [],
            "fixtures": [],
            "start_line": cls.start_line,
            "end_line": cls.end_line,
        }
    )
    return TestChunk(
        chunk_id=_chunk_id(rel_path, "tcl", cls.name),
        text="\n".join(header) + "\n\n" + (cls.docstring or f"{cls.name} 测试类。"),
        metadata=meta,
    )


def chunk_tests(
    root: str | Path = PROJECT_ROOT,
    *,
    excel_path: str | Path | None = None,
) -> list[TestChunk]:
    """切分测试用例：每个 test 函数一块，测试类头一块。

    ``excel_path`` 默认指向 ``docs/cases/ERP系统测试用例.xlsx``；文件缺失或解析
    失败时自动退化为纯代码切分（metadata 的 case/module/priority 为空）。
    """
    project_root = Path(root)
    test_dir = project_root / "testcases"
    chunks: list[TestChunk] = []
    if not test_dir.is_dir():
        return chunks

    resolved_excel = Path(excel_path) if excel_path else project_root / "docs" / "cases" / "ERP系统测试用例.xlsx"
    cases = _load_case_specs(resolved_excel)

    for path in sorted(test_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        rel_path = _rel(path, project_root)
        parsed = parse_test_file(path)

        for cls in parsed.classes:
            chunks.append(_test_class_chunk(cls, rel_path))
            for test in cls.tests:
                chunks.append(
                    _test_case_chunk(test, rel_path, cases.get(_case_id_of(test)))
                )

        for test in parsed.functions:
            chunks.append(_test_case_chunk(test, rel_path, cases.get(_case_id_of(test))))

    return chunks


# ---------------------------------------------------------------------------
# KnowledgeChunk
# ---------------------------------------------------------------------------


def _knowledge_meta(dc: DocumentChunk) -> dict:
    """knowledge 层的身份键 + 过滤键，长文本只在 text。"""
    return {
        "layer": "knowledge",
        "kind": dc.metadata.chunk_type.value,
        "file": dc.metadata.source_doc,
        "class": "",
        "name": "",
        "qualname": "",
        "module": dc.metadata.module,
        "priority": "",
        "automation_status": "",
        "case_id": "",
        "page": dc.metadata.page,
        "sheet": "",
        "markers": [],
        "fixtures": [],
        "scope": "",
        "chunk_index": dc.metadata.chunk_index,
        "is_plugin": dc.metadata.is_plugin,
        "content_hash": dc.content_hash,
        "start_line": 0,
        "end_line": 0,
    }


def _from_document_chunk(dc: DocumentChunk) -> KnowledgeChunk:
    """adapter：复用 Phase 2.5 的 DocumentChunk，主键原样透传。"""
    return KnowledgeChunk(
        chunk_id=dc.chunk_id,
        text=dc.content,
        metadata=_knowledge_meta(dc),
    )


def chunk_knowledge(
    docs_dir: str | Path = DOCS_DIR,
    *,
    include_pdf: bool = True,
) -> list[KnowledgeChunk]:
    """切分业务文档（MD / PDF）；Word 在 P3.2 补齐。"""
    docs_path = Path(docs_dir)
    if not docs_path.is_dir():
        return []

    processor = DocumentProcessor()
    chunks: list[KnowledgeChunk] = []

    for path in sorted(docs_path.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".docx":
            raise NotImplementedError(
                "Word 切分将在 P3.2 补齐：tools/document/docx_filtter.py"
            )
        if suffix == ".pdf" and not include_pdf:
            continue
        if suffix not in {".md", ".pdf"}:
            continue

        for dc in processor.process_file(path):
            chunks.append(_from_document_chunk(dc))

    return chunks


# ---------------------------------------------------------------------------
# 质量闸门与汇总
# ---------------------------------------------------------------------------


def validate_chunks(chunks: list[BaseChunk]) -> None:
    """chunk 出厂检查：主键唯一、ASCII、长度、非空文本、必需 metadata。"""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise ValueError(f"chunk_id 重复：{chunk.chunk_id}")
        seen.add(chunk.chunk_id)

        if not chunk.chunk_id.isascii():
            raise ValueError(f"chunk_id 含非 ASCII：{chunk.chunk_id}")
        if len(chunk.chunk_id) > 64:
            raise ValueError(f"chunk_id 超过 64 字符：{chunk.chunk_id}")
        if not chunk.text.strip():
            raise ValueError(f"chunk 文本为空：{chunk.chunk_id}")

        meta = chunk.metadata
        for key in ("layer", "kind", "file"):
            if not meta.get(key):
                raise ValueError(f"metadata 缺少 {key}：{chunk.chunk_id}")
        if not (meta.get("name") or meta.get("class")):
            raise ValueError(f"metadata 缺少 name/class：{chunk.chunk_id}")


def chunk_all(
    root: str | Path = PROJECT_ROOT,
    *,
    include_pdf: bool = True,
) -> dict[str, list[BaseChunk]]:
    """一次切完三层，返回 ``{"code": [...], "test": [...], "knowledge": [...]}``。"""
    project_root = Path(root)
    code = chunk_code(project_root)
    tests = chunk_tests(project_root)
    knowledge = chunk_knowledge(project_root / "docs", include_pdf=include_pdf)

    for name, chunks in (("code", code), ("test", tests), ("knowledge", knowledge)):
        try:
            validate_chunks(chunks)
        except ValueError as exc:
            raise ValueError(f"[{name}] {exc}") from exc

    return {"code": code, "test": tests, "knowledge": knowledge}


def write_json(
    chunks_by_layer: dict[str, list[BaseChunk]],
    out_dir: Path = CACHE_DIR,
) -> dict[str, Path]:
    """把三层 chunk 写成 JSON，供人工抽查或 P3.2 的 indexer 消费。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for layer, chunks in chunks_by_layer.items():
        path = out_dir / f"chunks_{layer}.json"
        payload = {
            "layer": layer,
            "count": len(chunks),
            "chunks": [chunk.to_dict() for chunk in chunks],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written[layer] = path
    return written


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="三层 chunk 切分（code / test / knowledge）")
    parser.add_argument("--write", action="store_true", help="写入 cache/chunks_{layer}.json")
    parser.add_argument("--no-pdf", action="store_true", help="跳过 PDF（不跑 OCR，调试用）")
    parser.add_argument("--only", choices=["code", "test", "knowledge"], help="只切某一层")
    args = parser.parse_args(argv)

    if args.only == "code":
        chunks_by_layer = {"code": chunk_code()}
    elif args.only == "test":
        chunks_by_layer = {"test": chunk_tests()}
    elif args.only == "knowledge":
        chunks_by_layer = {"knowledge": chunk_knowledge(include_pdf=not args.no_pdf)}
    else:
        chunks_by_layer = chunk_all(include_pdf=not args.no_pdf)

    total = 0
    for layer, chunks in chunks_by_layer.items():
        total += len(chunks)
        kinds: dict[str, int] = {}
        for chunk in chunks:
            kind = chunk.metadata.get("kind", "?")
            kinds[kind] = kinds.get(kind, 0) + 1
        print(f"{layer}: {len(chunks)} 条  {kinds}")
        for chunk in chunks[:3]:
            print(f"  - {chunk.chunk_id}")
    print(f"total: {total}")

    if args.write:
        for layer, path in write_json(chunks_by_layer).items():
            print(f"written: {path.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
