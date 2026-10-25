"""page 类分析、步骤匹配、控件缓存加载、代码写入工具。"""
from __future__ import annotations

import ast
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from agent.Agents.Base_Agent import PROJECT_ROOT, log


# ---------------------------------------------------------------------------
# page 类分析
# ---------------------------------------------------------------------------


def extract_step_descriptions(page_source: str) -> list[tuple[str, str]]:
    """提取 ``@allure.step("...")`` 的中文描述与方法名。"""
    pattern = re.compile(
        r'@allure\.step\("([^"]+)"\)\s*\n\s*def\s+([a-zA-Z_]\w*)\s*\('
    )
    return pattern.findall(page_source)


_STEP_VERBS = re.compile(r"^(点击|输入|选择|勾选|填写|打开|关闭|等待|查看|确认)")


def _normalize(text: str) -> str:
    """去掉步骤动词，便于「点击插入行」与「插入行」匹配。"""
    return _STEP_VERBS.sub("", (text or "").strip())


def covered(step: str, descriptions: list[tuple[str, str]], threshold: float = 0.7) -> bool:
    """步骤是否已被某个 ``@allure.step`` 描述覆盖。"""
    target = _normalize(step)
    for description, _ in descriptions:
        candidate = _normalize(description)
        if not target or not candidate:
            continue
        if target in candidate or candidate in target:
            return True
        if SequenceMatcher(None, target, candidate).ratio() >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# 控件缓存
# ---------------------------------------------------------------------------


def load_control_cache(module: str, page: str, state: str) -> list[dict]:
    """加载 ``cache/pages/{module}_{page}_{state}.json`` 中的控件列表。"""
    path = PROJECT_ROOT / "cache" / "pages" / f"{module}_{page}_{state}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("controls", [])


# ---------------------------------------------------------------------------
# 写文件
# ---------------------------------------------------------------------------


def strip_fence(code: str) -> str:
    """去掉 LLM 返回的代码块围栏。"""
    code = (code or "").strip()
    if code.startswith("```"):
        code = re.sub(r"^```[a-zA-Z]*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)
    return code.strip()


def indent_code(code: str, spaces: int = 4) -> str:
    """整体平移 ``spaces`` 格，保留代码块内部的相对缩进。"""
    prefix = " " * spaces
    return "\n".join(
        prefix + line if line.strip() else "" for line in code.splitlines()
    )


def _fix_top_decorator(code: str) -> str:
    """修复「装饰器顶格、后续行缩进 4」的常见 LLM 输出。

    ``@allure.story`` 顶格 + ``    @pytest`` / ``    def`` + ``        body``
    -> 所有行统一顶格后，方法体保留 4 空格相对缩进。
    """
    lines = code.splitlines()
    if not lines or not lines[0].lstrip().startswith("@"):
        return code

    fixed = [lines[0]]
    for line in lines[1:]:
        fixed.append(line[4:] if line.startswith("    ") else line)
    return "\n".join(fixed)


def _indent_candidates(code: str) -> list[str]:
    """按「原样 / 顶格 / 修复顶格装饰器」三种方式尝试缩进。"""
    return [
        indent_code(code),
        indent_code(_fix_top_decorator(code)),
        indent_code(code.lstrip()),
    ]


def append_code(path: Path, code: str) -> None:
    """把方法代码追加到文件末尾（缩进到类内），并做语法校验。"""
    code = strip_fence(code)
    if not code:
        log.warning("没有生成代码，跳过写入：%s", path)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    source = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""

    for candidate in _indent_candidates(code):
        merged = source + "\n\n" + candidate + "\n"
        try:
            ast.parse(merged)
        except SyntaxError as exc:
            log.debug("候选缩进语法错误（行 %s）：%s", exc.lineno, exc.msg)
            continue
        path.write_text(merged, encoding="utf-8")
        log.info("已追加 %s 行到 %s", len(candidate.splitlines()), path)
        return

    log.error("生成代码无法通过语法校验，已跳过写入：%s", path)
