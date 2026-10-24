"""AST 解析工具：把 Python 源码解析成结构化数据，供 ``agent/chunking.py`` 切分。

职责边界
--------
- **只解析，不切分、不嵌入、不写文件**
- 返回 dataclass，字段与 chunk 的 metadata 一一对应
- 同时可作为 Stage 3 的 grounding 工具被 LLM 调用：
  ``describe_page()`` / ``describe_conftest()`` 返回精简的、可 JSON 化的视图

为什么不把 AST 逻辑写在 chunking 里
-----------------------------------
页面对象与 conftest 的解析结果有独立价值：Stage 3 生成代码前需要
"这个 Page 有哪些公共方法"、"有哪些 fixture 可用"。把解析与切分分开后，
同一份解析结果既能喂给 chunker，也能直接作为 LLM 工具的输出。
"""
from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path


class AstParseError(ValueError):
    """源码无法解码或无法解析为 AST。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ParsedMethod:
    """一个函数/方法的结构化信息。"""

    name: str
    qualname: str
    signature: str
    docstring: str
    decorators: list[str] = field(default_factory=list)
    is_public: bool = True
    is_async: bool = False
    is_property: bool = False
    is_classmethod: bool = False
    is_staticmethod: bool = False
    start_line: int = 0
    end_line: int = 0
    source: str = ""


@dataclass
class ParsedConstant:
    """类常量（POM 的 locator、路径常量等）。"""

    name: str
    value: str
    start_line: int
    end_line: int
    source: str


@dataclass
class ParsedClass:
    """一个类及其方法、常量块。"""

    name: str
    qualname: str
    docstring: str
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    methods: list[ParsedMethod] = field(default_factory=list)
    # 连续的 Assign 语句会被合并为一个块，保留"属性区"的语义
    constant_blocks: list[list[ParsedConstant]] = field(default_factory=list)

    @property
    def public_methods(self) -> list[ParsedMethod]:
        return [m for m in self.methods if m.is_public]

    @property
    def constants(self) -> list[ParsedConstant]:
        return [c for block in self.constant_blocks for c in block]


@dataclass
class ParsedFixture:
    """一个 ``@pytest.fixture``。"""

    name: str
    signature: str
    params: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    scope: str = "function"
    autouse: bool = False
    docstring: str = ""
    start_line: int = 0
    end_line: int = 0
    source: str = ""


@dataclass
class ParsedTestCase:
    """一个 ``test_*`` 测试函数。"""

    name: str
    qualname: str
    class_name: str = ""
    signature: str = ""
    params: list[str] = field(default_factory=list)
    fixtures: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    parametrize: list[dict] = field(default_factory=list)
    allure: dict[str, str] = field(default_factory=dict)
    docstring: str = ""
    assert_messages: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    source: str = ""


@dataclass
class ParsedTestClass:
    """一个 ``Test*`` 测试类。"""

    name: str
    docstring: str
    decorators: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    tests: list[ParsedTestCase] = field(default_factory=list)


@dataclass
class ParsedTestFile:
    """一个测试文件的解析结果。"""

    classes: list[ParsedTestClass] = field(default_factory=list)
    functions: list[ParsedTestCase] = field(default_factory=list)

    @property
    def tests(self) -> list[ParsedTestCase]:
        result: list[ParsedTestCase] = []
        for cls in self.classes:
            result.extend(cls.tests)
        result.extend(self.functions)
        return result


@dataclass
class ParsedModule:
    """一个 Python 模块。"""

    path: str
    docstring: str
    classes: list[ParsedClass] = field(default_factory=list)
    functions: list[ParsedMethod] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

# pytest 内建 fixture：不视为"业务前置依赖"
_PYTEST_BUILTIN_FIXTURES: frozenset[str] = frozenset(
    {
        "request",
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "tmpdir_factory",
        "monkeypatch",
        "capsys",
        "capfd",
        "capfdbinary",
        "capsysbinary",
        "caplog",
        "pytestconfig",
        "recwarn",
        "cache",
        "doctest_namespace",
        "record_property",
        "record_xml_attribute",
        "record_testsuite_property",
        "testdir",
        "pytester",
        "linecomp",
    }
)


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AstParseError(f"无法解码 {path}: {exc}") from exc
    except OSError as exc:
        raise AstParseError(f"无法读取 {path}: {exc}") from exc


def parse_source(source: str, filename: str = "<string>") -> ast.Module:
    """把源码解析为 AST；语法错误统一转成 :class:`AstParseError`。"""
    try:
        return ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise AstParseError(f"{filename}:{exc.lineno}: {exc.msg}") from exc


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse 极少数节点会失败
        return "..."


def _docstring(node: ast.AST) -> str:
    try:
        return ast.get_docstring(node, clean=True) or ""
    except Exception:  # pragma: no cover
        return ""


def _decorator_text(dec: ast.expr) -> str:
    return f"@{_unparse(dec)}"


def _decorator_target(dec: ast.expr) -> ast.expr:
    """``@x.y(...)`` -> ``x.y``；``@x.y`` -> ``x.y``。"""
    return dec.func if isinstance(dec, ast.Call) else dec


def _decorator_names(node: ast.AST) -> list[str]:
    return [_decorator_text(d) for d in getattr(node, "decorator_list", [])]


def _has_decorator(node: ast.AST, *attrs: str) -> bool:
    for dec in getattr(node, "decorator_list", []):
        target = _decorator_target(dec)
        if isinstance(target, ast.Attribute) and target.attr in attrs:
            return True
        if isinstance(target, ast.Name) and target.id in attrs:
            return True
    return False


def _node_start(node: ast.AST) -> int:
    """节点起始行：含装饰器的第一行。"""
    start = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min(start, min(d.lineno for d in decorators))
    return start


def _node_source(lines: list[str], node: ast.AST) -> str:
    """按行回切节点源码，含装饰器。

    不做 ``textwrap.dedent``：dedent 会改写多行字符串字面量内部的缩进，
    而缩进是字符串值的一部分。保留原始缩进对嵌入模型没有影响。
    """
    start = _node_start(node)
    end = getattr(node, "end_lineno", None) or getattr(node, "lineno", start)
    return "\n".join(lines[start - 1 : end])


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """重建完整签名：位置参数、仅位置参数、*args、仅关键字参数、**kwargs、返回注解。"""
    args = node.args
    parts: list[str] = []

    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    first_default = len(positional) - len(defaults)
    posonly_count = len(args.posonlyargs)

    for index, arg in enumerate(positional):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_unparse(arg.annotation)}"
        if index >= first_default:
            text += f"={_unparse(defaults[index - first_default])}"
        parts.append(text)
        # 仅位置参数之后插入 "/"
        if posonly_count and index == posonly_count - 1:
            parts.append("/")

    if args.vararg is not None:
        text = f"*{args.vararg.arg}"
        if args.vararg.annotation is not None:
            text += f": {_unparse(args.vararg.annotation)}"
        parts.append(text)
    elif args.kwonlyargs:
        # 没有 *args 但有仅关键字参数时，用裸 "*" 分隔
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_unparse(arg.annotation)}"
        if default is not None:
            text += f"={_unparse(default)}"
        parts.append(text)

    if args.kwarg is not None:
        text = f"**{args.kwarg.arg}"
        if args.kwarg.annotation is not None:
            text += f": {_unparse(args.kwarg.annotation)}"
        parts.append(text)

    returns = f" -> {_unparse(node.returns)}" if node.returns is not None else ""
    return f"{node.name}({', '.join(parts)}){returns}"


def _parse_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    *,
    class_name: str = "",
) -> ParsedMethod:
    qualname = f"{class_name}.{node.name}" if class_name else node.name
    params = [a.arg for a in node.args.posonlyargs] + [a.arg for a in node.args.args]
    return ParsedMethod(
        name=node.name,
        qualname=qualname,
        signature=_format_signature(node),
        docstring=_docstring(node),
        decorators=_decorator_names(node),
        is_public=not node.name.startswith("_"),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_property=_has_decorator(node, "property", "cached_property"),
        is_classmethod=_has_decorator(node, "classmethod"),
        is_staticmethod=_has_decorator(node, "staticmethod"),
        start_line=_node_start(node),
        end_line=getattr(node, "end_lineno", None) or node.lineno,
        source=_node_source(lines, node),
    )


def _parse_constant(node: ast.Assign | ast.AnnAssign, lines: list[str]) -> ParsedConstant:
    if isinstance(node, ast.Assign):
        target = node.targets[0] if node.targets else None
        value = node.value
    else:
        target = node.target
        value = node.value

    if isinstance(target, ast.Name):
        name = target.id
    elif isinstance(target, ast.Tuple):
        name = ",".join(_unparse(e) for e in target.elts)
    else:
        name = _unparse(target)

    value_text = _unparse(value)
    if len(value_text) > 300:
        value_text = value_text[:300] + "..."

    return ParsedConstant(
        name=name,
        value=value_text,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", None) or node.lineno,
        source=_node_source(lines, node),
    )


def _parse_class(node: ast.ClassDef, lines: list[str]) -> ParsedClass:
    cls = ParsedClass(
        name=node.name,
        qualname=node.name,
        docstring=_docstring(node),
        bases=[_unparse(b) for b in node.bases],
        decorators=_decorator_names(node),
        start_line=_node_start(node),
        end_line=getattr(node, "end_lineno", None) or node.lineno,
    )

    current_block: list[ParsedConstant] = []
    for stmt in node.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            current_block.append(_parse_constant(stmt, lines))
            continue

        # 非赋值语句：先结束当前常量块，再处理方法
        if current_block:
            cls.constant_blocks.append(current_block)
            current_block = []

        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cls.methods.append(_parse_function(stmt, lines, class_name=node.name))

    if current_block:
        cls.constant_blocks.append(current_block)

    return cls


def _parse_module_level(node: ast.AST, lines: list[str]) -> ParsedModule:
    module = ParsedModule(path="", docstring=_docstring(node))
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, ast.ClassDef):
            module.classes.append(_parse_class(stmt, lines))
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module.functions.append(_parse_function(stmt, lines))
    return module


# ---------------------------------------------------------------------------
# 对外解析函数
# ---------------------------------------------------------------------------


def parse_module(path: str | Path) -> ParsedModule:
    """解析任意 Python 文件，返回模块级类与函数。"""
    file_path = Path(path)
    source = _read_source(file_path)
    tree = parse_source(source, filename=str(file_path))
    lines = source.splitlines()
    module = _parse_module_level(tree, lines)
    module.path = str(file_path)
    return module


def parse_page_object(path: str | Path) -> ParsedClass | None:
    """解析页面对象文件，返回第一个类（通常是 ``XxxPage``）。

    返回值包含全部方法（含私有）与常量块；调用方自行用
    ``public_methods`` 过滤公共方法。
    """
    module = parse_module(path)
    return module.classes[0] if module.classes else None


def parse_conftest(path: str | Path) -> list[ParsedFixture]:
    """解析 conftest.py，返回所有 ``@pytest.fixture``。

    ``depends_on`` 是从签名推导的前置 fixture（已排除 pytest 内建）。
    """
    file_path = Path(path)
    source = _read_source(file_path)
    tree = parse_source(source, filename=str(file_path))
    lines = source.splitlines()

    fixtures: list[ParsedFixture] = []
    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _has_decorator(stmt, "fixture"):
            continue

        params = [a.arg for a in stmt.args.posonlyargs] + [a.arg for a in stmt.args.args]
        scope = "function"
        autouse = False
        for dec in stmt.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                    scope = str(kw.value.value)
                elif kw.arg == "autouse" and isinstance(kw.value, ast.Constant):
                    autouse = bool(kw.value.value)

        fixtures.append(
            ParsedFixture(
                name=stmt.name,
                signature=_format_signature(stmt),
                params=params,
                depends_on=[p for p in params if p not in _PYTEST_BUILTIN_FIXTURES],
                scope=scope,
                autouse=autouse,
                docstring=_docstring(stmt),
                start_line=_node_start(stmt),
                end_line=getattr(stmt, "end_lineno", None) or stmt.lineno,
                source=_node_source(lines, stmt),
            )
        )
    return fixtures


def _extract_parametrize(node: ast.AST) -> list[dict]:
    cases: list[dict] = []
    for dec in getattr(node, "decorator_list", []):
        target = _decorator_target(dec)
        if not (isinstance(target, ast.Attribute) and target.attr == "parametrize"):
            continue
        if not isinstance(dec, ast.Call) or not dec.args:
            continue

        argnames_node = dec.args[0]
        if isinstance(argnames_node, ast.Constant):
            argnames = str(argnames_node.value)
        else:
            argnames = _unparse(argnames_node)

        argvalues = ""
        if len(dec.args) > 1:
            argvalues = _unparse(dec.args[1])
            if len(argvalues) > 300:
                argvalues = argvalues[:300] + "..."

        ids = ""
        for kw in dec.keywords:
            if kw.arg == "ids":
                ids = _unparse(kw.value)
                if len(ids) > 200:
                    ids = ids[:200] + "..."

        cases.append({"argnames": argnames, "argvalues": argvalues, "ids": ids})
    return cases


def _extract_allure(node: ast.AST) -> dict[str, str]:
    meta: dict[str, str] = {}
    for dec in getattr(node, "decorator_list", []):
        target = _decorator_target(dec)
        if not isinstance(target, ast.Attribute) or target.attr not in {
            "story",
            "feature",
            "title",
            "severity",
            "epic",
        }:
            continue
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        arg = dec.args[0]
        if isinstance(arg, ast.Constant):
            meta[target.attr] = str(arg.value)
        else:
            meta[target.attr] = _unparse(arg)
    return meta


def _extract_markers(node: ast.AST) -> list[str]:
    markers: list[str] = []
    for dec in getattr(node, "decorator_list", []):
        target = _decorator_target(dec)
        if not isinstance(target, ast.Attribute):
            continue
        if not isinstance(target.value, ast.Attribute) or target.value.attr != "mark":
            continue
        if target.attr == "parametrize":
            continue
        markers.append(target.attr)
    return markers


def _extract_assert_messages(node: ast.AST) -> list[str]:
    messages: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Assert) or child.msg is None:
            continue
        if isinstance(child.msg, ast.Constant) and isinstance(child.msg.value, str):
            text = child.msg.value.strip()
            if text:
                messages.append(text[:160])
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for msg in messages:
        if msg not in seen:
            seen.add(msg)
            unique.append(msg)
    return unique


def _parse_test_case(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    *,
    class_name: str = "",
) -> ParsedTestCase:
    qualname = f"{class_name}.{node.name}" if class_name else node.name
    params = [a.arg for a in node.args.posonlyargs] + [a.arg for a in node.args.args]

    parametrize = _extract_parametrize(node)
    # parametrize 的参数是数据占位符，不是 fixture，不能混进 fixtures
    parametrized_names = {
        name.strip()
        for case in parametrize
        for name in str(case.get("argnames", "")).split(",")
        if name.strip()
    }
    fixtures = [p for p in params if p != "self" and p not in parametrized_names]

    return ParsedTestCase(
        name=node.name,
        qualname=qualname,
        class_name=class_name,
        signature=_format_signature(node),
        params=params,
        fixtures=fixtures,
        markers=_extract_markers(node),
        parametrize=parametrize,
        allure=_extract_allure(node),
        docstring=_docstring(node),
        assert_messages=_extract_assert_messages(node),
        start_line=_node_start(node),
        end_line=getattr(node, "end_lineno", None) or node.lineno,
        source=_node_source(lines, node),
    )


def parse_test_file(path: str | Path) -> ParsedTestFile:
    """解析测试文件，返回测试类与模块级测试函数。"""
    file_path = Path(path)
    source = _read_source(file_path)
    tree = parse_source(source, filename=str(file_path))
    lines = source.splitlines()
    result = ParsedTestFile()

    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            test_class = ParsedTestClass(
                name=stmt.name,
                docstring=_docstring(stmt),
                decorators=_decorator_names(stmt),
                start_line=_node_start(stmt),
                end_line=getattr(stmt, "end_lineno", None) or stmt.lineno,
            )
            for child in stmt.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith(
                    "test"
                ):
                    test_class.tests.append(
                        _parse_test_case(child, lines, class_name=stmt.name)
                    )
            result.classes.append(test_class)

        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith(
            "test"
        ):
            result.functions.append(_parse_test_case(stmt, lines))

    return result


# ---------------------------------------------------------------------------
# LLM 工具接口：精简、可 JSON 化
# ---------------------------------------------------------------------------


def describe_page(path: str | Path) -> dict:
    """返回页面对象的公共方法签名与文档字符串，供 LLM 生成代码前核对 API。

    这是 Astra ``list_page_methods()`` 的等价实现。
    """
    cls = parse_page_object(path)
    if cls is None:
        return {"class": "", "docstring": "", "methods": [], "constants": {}}

    return {
        "class": cls.name,
        "docstring": cls.docstring,
        "bases": cls.bases,
        "methods": [
            {
                "name": method.name,
                "signature": method.signature,
                "docstring": method.docstring,
                "decorators": method.decorators,
                "is_property": method.is_property,
                "start_line": method.start_line,
            }
            for method in cls.public_methods
        ],
        "constants": {c.name: c.value for c in cls.constants},
    }


def describe_conftest(path: str | Path) -> dict:
    """返回 conftest.py 中所有 fixture 的名称、参数与前置依赖。

    这是 Astra ``get_fixtures()`` 的等价实现。
    """
    return {
        "fixtures": [
            {
                "name": fixture.name,
                "params": fixture.params,
                "depends_on": fixture.depends_on,
                "scope": fixture.scope,
                "autouse": fixture.autouse,
                "docstring": fixture.docstring,
            }
            for fixture in parse_conftest(path)
        ]
    }
