"""文件读取、写入、编辑工具。

使用 LangChain @tool 装饰器实现。
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool
from pydantic import BaseModel, Field

from tool.context import get_context

_MAX_READ_CHARS = 120_000
_MAX_CODE_INPUT_CHARS = 120_000
_ARTIFACT_SUBDIRS = ("controls", "dom", "cases")

# .env、私钥、git 元数据不进入模型上下文。
_SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def _is_sensitive(target: Path) -> bool:
    name = target.name.lower()
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return True
    if target.suffix.lower() in _SENSITIVE_SUFFIXES:
        return True
    return ".git" in {part.lower() for part in target.parts}


def _resolve(path: str, *, must_exist: bool = False) -> Path:
    """解析路径，限制在 workspace 内，并拦截敏感文件。"""
    workspace = get_context().workspace
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    if not target.is_relative_to(workspace):
        raise ValueError(f"路径必须在 workspace 内：{workspace}")
    if _is_sensitive(target):
        raise ValueError("禁止访问敏感文件")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    return target


def _resolve_dir(path: str) -> Path:
    """解析目录路径，限制在 workspace 内。"""
    workspace = get_context().workspace
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    if not target.is_relative_to(workspace):
        raise ValueError(f"路径必须在 workspace 内：{workspace}")
    if _is_sensitive(target):
        raise ValueError("禁止访问敏感目录")
    if not target.is_dir():
        raise FileNotFoundError(f"目录不存在：{target}")
    return target


def _relative(path: Path, workspace: Path) -> str:
    """尽量返回相对 workspace 的路径，便于模型/日志阅读。"""
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_optional(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    if len(text) > _MAX_CODE_INPUT_CHARS:
        text = text[:_MAX_CODE_INPUT_CHARS] + "\n...[内容过长，已截断]"
    return text


def _json_or_text(text: str) -> object:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _names(directory: Path, limit: int = 30) -> list[str]:
    try:
        return sorted(item.name for item in directory.iterdir() if item.is_file())[:limit]
    except OSError:
        return []


# ----------------------------------------------------------------------
# read_file
# ----------------------------------------------------------------------


class ReadFileInput(BaseModel):
    path: str = Field(description="文件路径，相对于 workspace 或 workspace 内的绝对路径")
    offset: int = Field(default=1, description="起始行，1-based", ge=1)
    limit: int = Field(default=2000, description="最多读取行数", ge=1)


@tool(args_schema=ReadFileInput)
def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
    """读取文本文件，返回带行号的内容。"""
    try:
        target = _resolve(path, must_exist=True)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, offset - 1)
        selected = lines[start:start + limit]
        if not selected:
            return "(空文件或超出范围)"
        text = "\n".join(f"{start + i + 1}|{line}" for i, line in enumerate(selected))
        if len(text) > _MAX_READ_CHARS:
            text = text[:_MAX_READ_CHARS] + "\n...[读取内容过长，已截断]"
        return text
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# write_file
# ----------------------------------------------------------------------


class WriteFileInput(BaseModel):
    path: str = Field(description="文件路径，相对于 workspace 或 workspace 内的绝对路径")
    content: str = Field(description="完整文件内容")


@tool(args_schema=WriteFileInput)
def write_file(path: str, content: str) -> str:
    """写入完整文本文件；父目录不存在会自动创建。"""
    try:
        target = _resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {target}（{len(content)} 字符）"
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# edit_file
# ----------------------------------------------------------------------


class EditFileInput(BaseModel):
    path: str = Field(description="文件路径，相对于 workspace 或 workspace 内的绝对路径")
    old_string: str = Field(description="要被替换的原文")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(default=False, description="是否替换全部匹配")


@tool(args_schema=EditFileInput)
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """精确替换文件中的文本；默认要求原文只出现一次。"""
    try:
        if not old_string:
            return "错误：old_string 不能为空"
        target = _resolve(path, must_exist=True)
        content = target.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)
        if count == 0:
            return f"错误：未找到原文：{old_string[:80]!r}"
        if count > 1 and not replace_all:
            return f"错误：原文出现 {count} 次；请加长原文或设置 replace_all=true"
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        target.write_text(updated, encoding="utf-8")
        return f"已编辑 {target}（替换 {count if replace_all else 1} 处）"
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# list_dir
# ----------------------------------------------------------------------


class ListDirInput(BaseModel):
    path: str = Field(default=".", description="目录路径，相对于 workspace")
    max_entries: int = Field(default=200, description="最多返回条目数", ge=1, le=1000)


@tool(args_schema=ListDirInput)
def list_dir(path: str = ".", max_entries: int = 200) -> str:
    """列出 workspace 内目录的文件和子目录（不递归）。"""
    try:
        target = _resolve_dir(path)
        workspace = get_context().workspace
        items = sorted(
            target.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        entries = [
            {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
            }
            for item in items[:max_entries]
        ]
        relative = target.relative_to(workspace)
        return json.dumps({
            "path": relative.as_posix() if str(relative) != "." else ".",
            "entries": entries,
            "count": len(entries),
            "truncated": len(items) > max_entries,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------
# load_code_inputs
# ----------------------------------------------------------------------


class LoadCodeInputsInput(BaseModel):
    module: str = Field(description="目标模块名，例如 采购订单")


def _safe_module_name(module: str) -> str:
    name = (module or "").strip()
    if not name or name in {".", ".."}:
        raise ValueError("module 不能为空")
    if "/" in name or "\\" in name or Path(name).name != name:
        raise ValueError("module 不能包含路径分隔符")
    return name


@tool(args_schema=LoadCodeInputsInput)
def load_code_inputs(module: str) -> str:
    """读取 CodeAgent 的控件与用例输入。

    路径以当前 ``ToolContext.artifacts`` 为根，开发/测试环境自动隔离：
    优先 ``controls/<module>.json``，其次 ``dom/<module>_facts_compact.json``；
    用例读 ``cases/<module>_cases.json``。
    """
    try:
        name = _safe_module_name(module)
        ctx = get_context()
        root = Path(ctx.artifacts)
        controls_path = root / "controls" / f"{name}.json"
        compact_path = root / "dom" / f"{name}_facts_compact.json"
        cases_path = root / "cases" / f"{name}_cases.json"

        controls_text = _read_optional(controls_path)
        controls_source = _relative(controls_path, ctx.workspace)
        if controls_text is None:
            controls_text = _read_optional(compact_path)
            controls_source = _relative(compact_path, ctx.workspace)
        if controls_text is None:
            return json.dumps({
                "error": f"未找到 {name} 的控件文件",
                "expected": [
                    _relative(controls_path, ctx.workspace),
                    _relative(compact_path, ctx.workspace),
                ],
                "available": {
                    subdir: _names(root / subdir) for subdir in _ARTIFACT_SUBDIRS
                },
            }, ensure_ascii=False)

        cases_text = _read_optional(cases_path)
        if cases_text is None:
            xlsx_files = sorted(
                _relative(path, ctx.workspace)
                for path in (root / "cases").glob(f"{name}*.xlsx")
            )
            return json.dumps({
                "error": f"未找到 {name} 的 JSON 用例文件",
                "expected": _relative(cases_path, ctx.workspace),
                "controls_source": controls_source,
                "controls": _json_or_text(controls_text),
                "available_cases": _names(root / "cases"),
                "xlsx": xlsx_files,
            }, ensure_ascii=False)

        return json.dumps({
            "module": name,
            "artifacts_root": _relative(root, ctx.workspace),
            "controls_source": controls_source,
            "cases_source": _relative(cases_path, ctx.workspace),
            "controls": _json_or_text(controls_text),
            "cases": _json_or_text(cases_text),
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


__all__ = [
    "edit_file",
    "list_dir",
    "load_code_inputs",
    "read_file",
    "write_file",
]
