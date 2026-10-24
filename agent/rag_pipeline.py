"""RAG 流水线：路由 → 检索 → 冲突检测 → 提示词 → 流式生成。

流程（用管道符编排）::

    Pipeline()
      | RouteStep()       # 1. 判断三类路由
      | RetrieveStep()    # 2. 按路由权重检索三层
      | ConflictStep()    # 3. 冲突检测，命中则中止
      | PromptStep()      # 4. 拼提示词
      | GenerateStep()    # 5. 调 LLM，流式输出

三类路由
--------
| 路由 | 输入特征 | 产出 | 三层权重（code/test/knowledge） |
|---|---|---|---|
| ``code_gen`` | "按测试用例生成测试代码" | pytest 代码 | 8 / 4 / 2 |
| ``case_xlsx`` | "补充/设计 Excel 用例" | JSON 用例行 | 2 / 6 / 4 |
| ``new_module`` | "销售模块的用例"（知识库无代码） | JSON 用例行 | 0 / 2 / 8 |

归不进以上三类时返回 ``unsupported``，直接回答"无法做到"，不调 LLM。

冲突检测
--------
以下任一成立即中止生成并给出人工审核原因：

1. retriever 的方法存在性检查命中（测试调用了不存在的 Page 方法）；
2. 三层检索全空；
3. ``code_gen`` 路由但 code 层没有页面方法。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Protocol

from pathlib import Path

import httpx

from agent.config import Settings, get_settings
from agent.milvus_connect import get_store
from agent.retriever import RetrievalResult, Retriever
from agent.web_search import is_configured, search_text
from agent.xlsx_writer import write_generated_cases

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


class Route(str, Enum):
    """三类路由 + 不支持。"""

    CODE_GEN = "code_gen"
    CASE_XLSX = "case_xlsx"
    NEW_MODULE = "new_module"
    UNSUPPORTED = "unsupported"


@dataclass
class RouteDecision:
    route: Route
    module: str = ""
    sheet: str = ""
    reason: str = ""


# 每个路由的三层检索配额：code / test / knowledge
ROUTE_WEIGHTS: dict[Route, dict[str, int]] = {
    Route.CODE_GEN: {"code": 8, "test": 4, "knowledge": 2},
    Route.CASE_XLSX: {"code": 5, "test": 5, "knowledge": 3},
    Route.NEW_MODULE: {"code": 0, "test": 2, "knowledge": 8},
    Route.UNSUPPORTED: {"code": 0, "test": 0, "knowledge": 0},
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# module -> Excel 工作表（LLM 常把 module 填进 sheet，这里做兜底映射）
SHEET_BY_MODULE: dict[str, str] = {
    "采购管理": "采购页",
    "零售管理": "零售页",
    "登录": "登录页",
}

_SHEET_CACHE: list[str] | None = None


def _known_sheets() -> list[str]:
    """读取 Excel 的工作表名，供路由校验用。"""
    global _SHEET_CACHE
    if _SHEET_CACHE is not None:
        return _SHEET_CACHE

    path = PROJECT_ROOT / "docs" / "cases" / "ERP系统测试用例.xlsx"
    if not path.exists():
        _SHEET_CACHE = []
        return _SHEET_CACHE
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True)
        try:
            _SHEET_CACHE = list(workbook.sheetnames)
        finally:
            workbook.close()
    except Exception:  # noqa: BLE001 - 读不到就退化为空列表
        _SHEET_CACHE = []
    return _SHEET_CACHE


# ---------------------------------------------------------------------------
# LLM 客户端（DashScope 的 OpenAI 兼容接口，SSE 流式）
# ---------------------------------------------------------------------------


class LLMClient:
    """最小的 OpenAI 兼容客户端，只提供 chat / stream。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.require_llm()

    def _url(self) -> str:
        return self.settings.llm_base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict],
        *,
        strong: bool,
        temperature: float | None,
        stream: bool,
    ) -> dict:
        return {
            "model": self.settings.llm_strong_model if strong else self.settings.llm_fast_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": stream,
        }

    def chat(
        self,
        messages: list[dict],
        *,
        strong: bool | None = None,
        temperature: float | None = None,
        max_attempts: int = 5,
        switch_after: int = 2,
    ) -> str:
        """非流式调用。

        ``strong=None`` 表示自动：先用 flash，连续失败 ``switch_after`` 次后切 max；
        总尝试次数不超过 ``max_attempts``。
        """
        use_strong = bool(strong)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                with httpx.Client(timeout=120) as client:
                    response = client.post(
                        self._url(),
                        headers=self._headers(),
                        json=self._payload(
                            messages,
                            strong=use_strong,
                            temperature=temperature,
                            stream=False,
                        ),
                    )
                    response.raise_for_status()
                    data = response.json()
                return data["choices"][0]["message"]["content"] or ""
            except Exception as exc:  # noqa: BLE001 - 需要按次数重试
                last_error = exc
                log.warning(
                    "LLM chat 失败（第 %s/%s 次，strong=%s）：%s",
                    attempt,
                    max_attempts,
                    use_strong,
                    exc,
                )
                if strong is None and attempt >= switch_after:
                    use_strong = True

        raise RuntimeError(f"LLM chat 连续 {max_attempts} 次失败：{last_error!r}")

    def stream(
        self,
        messages: list[dict],
        *,
        strong: bool | None = None,
        temperature: float | None = None,
        max_attempts: int = 5,
        switch_after: int = 2,
    ) -> Iterator[str]:
        """流式调用。

        - 还没输出任何 chunk 时失败：允许重试，失败 2 次后切 max；
        - 已经开始输出：无法回滚，直接抛出，由调用方处理。
        """
        use_strong = bool(strong)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            yielded = False
            try:
                for chunk in self._stream_once(
                    messages,
                    strong=use_strong,
                    temperature=temperature,
                ):
                    yielded = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning(
                    "LLM stream 失败（第 %s/%s 次，strong=%s，已输出=%s）：%s",
                    attempt,
                    max_attempts,
                    use_strong,
                    yielded,
                    exc,
                )
                if yielded:
                    raise RuntimeError(f"流式输出中断（已输出部分内容）：{exc}") from exc
                if strong is None and attempt >= switch_after:
                    use_strong = True

        raise RuntimeError(f"LLM stream 连续 {max_attempts} 次失败：{last_error!r}")

    def _stream_once(
        self,
        messages: list[dict],
        *,
        strong: bool,
        temperature: float | None,
    ) -> Iterator[str]:
        payload = self._payload(messages, strong=strong, temperature=temperature, stream=True)
        with httpx.stream(
            "POST",
            self._url(),
            headers=self._headers(),
            json=payload,
            timeout=httpx.Timeout(180, connect=10),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # DashScope 结束时可能发 {"choices": [], "usage": {...}}
                choices = chunk.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta


# ---------------------------------------------------------------------------
# 管道
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """管道内共享的状态。"""

    query: str
    module: str = ""
    sheet: str = ""
    decision: RouteDecision | None = None
    retrieval: RetrievalResult | None = None
    conflicts: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_message: str = ""
    messages: list[dict] = field(default_factory=list)
    context_text: str = ""
    web_context: str = ""


class Step(Protocol):
    def __call__(self, ctx: PipelineContext) -> PipelineContext: ...


class Pipeline:
    """用 ``|`` 串联步骤；最后一步返回流式生成器。"""

    def __init__(self, *steps: Step):
        self.steps = steps

    def __or__(self, step: Step) -> "Pipeline":
        return Pipeline(*self.steps, step)

    def prepare(self, query: str, **kwargs) -> PipelineContext:
        """执行最后一个步骤之前的所有步骤。"""
        ctx = PipelineContext(query=query, **kwargs)
        for step in self.steps[:-1]:
            ctx = step(ctx)
            if ctx.aborted:
                break
        return ctx

    def stream(self, query: str, *, on_complete=None, **kwargs) -> Iterator[str]:
        ctx = self.prepare(query, **kwargs)
        if ctx.aborted:
            yield ctx.abort_message
            return

        buffer: list[str] = []
        for chunk in self.steps[-1](ctx):
            buffer.append(chunk)
            yield chunk

        ctx.response = "".join(buffer)
        if on_complete is not None:
            yield from on_complete(ctx)


# ---------------------------------------------------------------------------
# 步骤 1：路由
# ---------------------------------------------------------------------------


class RouteStep:
    """LLM 语义路由；LLM 不可用时回退关键词规则。"""

    def __init__(self, llm: LLMClient, store):
        self.llm = llm
        self.store = store

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        ctx.decision = self._decide(ctx.query)
        ctx.module = ctx.module or ctx.decision.module
        ctx.sheet = ctx.sheet or ctx.decision.sheet
        log.info("路由：%s | module=%s sheet=%s", ctx.decision.route.value, ctx.module, ctx.sheet)
        return ctx

    def _decide(self, query: str) -> RouteDecision:
        try:
            modules = self._known_modules()
            raw = self.llm.chat(
                self._messages(query, modules),
                strong=False,
                temperature=0.0,
            )
            decision = self._parse(raw)
            if decision is not None:
                return self._normalize_sheet(decision)
        except Exception as exc:  # noqa: BLE001 - 路由失败必须能回退
            log.warning("路由 LLM 失败，回退规则：%s", exc)
        return self._normalize_sheet(self._rule(query))

    @staticmethod
    def _normalize_sheet(decision: RouteDecision) -> RouteDecision:
        """LLM 常把 module 填进 sheet；校验无效时按 module 映射。"""
        sheets = _known_sheets()
        if decision.sheet and (not sheets or decision.sheet in sheets):
            return decision
        mapped = SHEET_BY_MODULE.get(decision.module, "")
        if mapped and (not sheets or mapped in sheets):
            decision.sheet = mapped
        return decision

    def _known_modules(self) -> list[str]:
        rows = self.store.query(
            "erp_code",
            'layer == "code"',
            output_fields=["page"],
            limit=500,
        )
        return sorted({row.get("page", "") for row in rows if row.get("page")})

    @staticmethod
    def _messages(query: str, modules: list[str]) -> list[dict]:
        system = (
            "你是 ERP 测试生成系统的路由器。只输出一个 JSON 对象，不要解释。\n"
            f"知识库已有代码/用例的模块：{', '.join(modules) or '无'}。\n"
            f"已有 Excel 工作表：{', '.join(_known_sheets()) or '无'}。\n"
            "三类任务：\n"
            "1. code_gen：基于已有页面对象和用例，生成 pytest 测试代码。\n"
            "2. case_xlsx：基于已有用例和页面控件，补充/生成 Excel 测试用例。\n"
            "3. new_module：用户指定的模块在知识库中没有代码/用例，生成 Excel 用例。\n"
            "无法归入以上三类时输出 unsupported（例如问怎么用、闲聊、与测试无关）。\n"
            '输出格式：{"route":"...","module":"...","sheet":"...","reason":"..."}\n'
            "注意：sheet 必须从上面的工作表里选，不要把 module 填进 sheet。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]

    @staticmethod
    def _parse(raw: str) -> RouteDecision | None:
        text = raw.strip()
        if "```" in text:
            text = re.sub(r"```(?:json)?|```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        return RouteDecision(
            route=Route(str(data.get("route", "")).strip()),
            module=str(data.get("module", "") or "").strip(),
            sheet=str(data.get("sheet", "") or "").strip(),
            reason=str(data.get("reason", "") or "").strip(),
        )

    @staticmethod
    def _rule(query: str) -> RouteDecision:
        q = query.lower()
        test_words = ("测试", "用例", "case", "test", "代码", "自动化")
        if not any(word in q for word in test_words):
            return RouteDecision(Route.UNSUPPORTED, reason="规则：没有测试相关意图")
        if any(word in q for word in ("代码", "pytest", "test_", "脚本")):
            return RouteDecision(Route.CODE_GEN, reason="规则：包含代码关键词")
        if any(word in q for word in ("用例", "xlsx", "表格", "补充", "设计")):
            return RouteDecision(Route.CASE_XLSX, reason="规则：包含用例关键词")
        return RouteDecision(Route.NEW_MODULE, reason="规则：默认新模块")


# ---------------------------------------------------------------------------
# 步骤 2：检索
# ---------------------------------------------------------------------------


class RetrieveStep:
    def __init__(self, retriever: Retriever):
        self.retriever = retriever

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.decision is None or ctx.decision.route == Route.UNSUPPORTED:
            ctx.retrieval = RetrievalResult()
            return ctx

        weights = ROUTE_WEIGHTS[ctx.decision.route]
        ctx.retrieval = self.retriever.collect(
            ctx.query,
            pages=[ctx.module] if ctx.module else [],
            module=ctx.module,
            sheet=ctx.sheet,
            limits=weights,
        )
        log.info(
            "检索：code=%s test=%s knowledge=%s",
            len(ctx.retrieval.page_methods),
            len(ctx.retrieval.similar_tests),
            len(ctx.retrieval.business_knowledge),
        )
        return ctx


# ---------------------------------------------------------------------------
# 步骤 3：冲突检测
# ---------------------------------------------------------------------------


class WebSearchStep:
    """new_module 路由补外部业务流程；其他路由跳过，失败降级为空。"""

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.decision is None or ctx.decision.route != Route.NEW_MODULE:
            return ctx
        if not is_configured():
            log.info("未配置 DEEPSEEK_API_KEY，跳过 web 搜索")
            return ctx
        query = f"{ctx.module or ctx.query} ERP 业务流程 测试点"
        ctx.web_context = search_text(query, count=5)
        log.info("web 搜索：%s -> %s 字符", query, len(ctx.web_context))
        return ctx


class ConflictStep:
    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.decision is None or ctx.decision.route == Route.UNSUPPORTED:
            ctx.aborted = True
            ctx.abort_message = (
                "无法做到：这个问题不属于测试用例/测试代码生成任务。\n"
                "当前只支持：生成测试代码、生成/补充 Excel 用例、为新模块生成 Excel 用例。"
            )
            return ctx

        conflicts: list[str] = []
        result = ctx.retrieval
        if result is not None:
            conflicts.extend(result.warnings)
            if not (result.page_methods or result.similar_tests or result.business_knowledge):
                conflicts.append("三层检索结果均为空，缺少生成依据")
            if ctx.decision.route == Route.CODE_GEN and not result.page_methods:
                conflicts.append("代码生成需要页面方法，但 code 层没有检索到")

        ctx.conflicts = conflicts
        if conflicts:
            ctx.aborted = True
            ctx.abort_message = (
                "检测到冲突，已中止生成，请人工审核：\n"
                + "\n".join(f"- {item}" for item in conflicts)
            )
        return ctx


# ---------------------------------------------------------------------------
# 步骤 4：提示词
# ---------------------------------------------------------------------------


def _next_case_id(sheet: str) -> str:
    """读取源 Excel 指定 sheet 的最大编号，返回下一个可用编号（如 采购-044）。"""
    path = PROJECT_ROOT / "docs" / "cases" / "ERP系统测试用例.xlsx"
    if not path.exists() or not sheet:
        return ""
    prefix = "采购" if "采购" in sheet else "零售" if "零售" in sheet else ""
    if not prefix:
        return ""

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True)
        try:
            worksheet = workbook[sheet] if sheet in workbook.sheetnames else workbook.active
            max_number = 0
            for row in worksheet.iter_rows(min_col=1, max_col=1, values_only=True):
                match = re.match(rf"^{prefix}-(\d+)$", str(row[0] or "").strip())
                if match:
                    max_number = max(max_number, int(match.group(1)))
        finally:
            workbook.close()
    except Exception:  # noqa: BLE001
        return ""
    return f"{prefix}-{max_number + 1:03d}"


class PromptStep:
    SYSTEM: dict[Route, str] = {
        Route.CODE_GEN: (
            "你是 ERP UI 自动化测试的代码生成器。硬性规则：\n"
            "1. 只能调用下面给出的 Page 方法，不要自创方法名。\n"
            "2. 不要写 page.locator / page.get_by_* 等原始定位，全部通过 Page 方法。\n"
            "3. 每个测试函数必须有明确的数据来源；参数化优先用 @pytest.mark.parametrize。\n"
            "4. 不要硬编码 URL 和账号。\n"
            "5. 输出一个完整的 pytest 测试类，放在 ```python 代码块里。"
        ),
        Route.CASE_XLSX: (
            "你是 ERP 测试用例设计器，为 Ant Design Vue 的 ERP 系统补充 Excel 用例。\n"
            "硬性规则：\n"
            "1. 只输出 JSON 数组，最多 5 条，不要解释。\n"
            "2. 字段：id / module / priority / purpose / preconditions / steps / "
            "controls / expected_result / assertions / automation_status。\n"
            "3. controls 是数组，每项 {\"name\": \"控件中文名\", \"action\": \"click|fill|select|check|wait\"}。\n"
            "4. 控件名必须来自参考资料的 locator、@allure.step 或已有用例；"
            "资料里找不到时写“待确认控件”，禁止编造。\n"
            "5. steps 只写业务操作，禁止出现方法名（如 xxx()）、变量名或代码；"
            "每一步都要能对应到一个控件。\n"
            "6. automation_status 固定为“待生成”。\n"
            "7. id 从用户提示里的“下一条可用编号”开始连续编号，不要与已有编号重复。\n"
            "8. steps 是字符串数组，每一项是一步操作。\n"
            "9. 输出前自查：每条用例的 controls 是否都能在参考资料里找到依据。"
        ),
        Route.NEW_MODULE: (
            "你是 ERP 测试用例设计器，正在为一个知识库没有代码/用例的新模块设计用例。\n"
            "规则：\n"
            "1. 只输出 JSON 数组，最多 5 条。\n"
            "2. 字段：id / module / priority / purpose / preconditions / steps / "
            "controls / expected_result / assertions / automation_status。\n"
            "3. controls 每项 {\"name\": \"控件中文名\", \"action\": \"click|fill|select|check|wait\"}；"
            "没有页面信息时写“待确认控件”，不要编造具体 locator。\n"
            "4. steps 只写业务操作，禁止出现方法名或代码。\n"
            "5. automation_status 固定为“待生成”。\n"
            "6. 在 JSON 前用一句话说明该模块缺少 Page Object。"
        ),
    }

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        route = ctx.decision.route
        context = ctx.retrieval.to_manual() if ctx.retrieval else ""
        ctx.context_text = context
        ctx.messages = [
            {"role": "system", "content": self.SYSTEM[route]},
            {"role": "user", "content": self._user_prompt(ctx, context)},
        ]
        return ctx

    @staticmethod
    def _user_prompt(ctx: PipelineContext, context: str) -> str:
        parts = [
            f"任务：{ctx.query}",
            f"目标模块：{ctx.module or '未指定'}",
            f"目标工作表：{ctx.sheet or '未指定'}",
        ]
        if ctx.decision and ctx.decision.reason:
            parts.append(f"路由依据：{ctx.decision.reason}")
        next_id = _next_case_id(ctx.sheet)
        if next_id:
            parts.append(f"下一条可用编号：{next_id}（从这里开始连续编号，不要重复已有编号）")
        if context:
            parts.append("参考资料：\n" + context)
        if ctx.web_context:
            parts.append("外部资料（web 搜索，仅供参考）：\n" + ctx.web_context)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 步骤 5：流式生成
# ---------------------------------------------------------------------------


class GenerateStep:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def __call__(self, ctx: PipelineContext) -> Iterator[str]:
        # 默认 flash；连续失败两次后由 LLMClient 自动切 max
        yield from self.llm.stream(ctx.messages, strong=None)


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------


def _extract_cases(text: str) -> list[dict]:
    """从 LLM 输出里提取 JSON 用例数组（容忍 ```json 围栏和前后说明文字）。"""
    body = (text or "").strip()
    if "```" in body:
        body = re.sub(r"```(?:json)?|```", "", body).strip()
    match = re.search(r"\[.*\]", body, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)]


class RAGPipeline:
    """对外入口：一条 query 进来，流式文本出去。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        retriever: Retriever | None = None,
        store=None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        self.retriever = retriever or Retriever()
        self.store = store or get_store()
        self.pipeline = (
            Pipeline()
            | RouteStep(self.llm, self.store)
            | RetrieveStep(self.retriever)
            | WebSearchStep()
            | ConflictStep()
            | PromptStep()
            | GenerateStep(self.llm)
        )

    def prepare(self, query: str, *, module: str = "", sheet: str = "") -> PipelineContext:
        return self.pipeline.prepare(query, module=module, sheet=sheet)

    def stream(self, query: str, *, module: str = "", sheet: str = "") -> Iterator[str]:
        return self.pipeline.stream(
            query,
            module=module,
            sheet=sheet,
            on_complete=self._persist,
        )

    def run(self, query: str, *, module: str = "", sheet: str = "") -> str:
        return "".join(self.stream(query, module=module, sheet=sheet))

    def _persist(self, ctx: PipelineContext) -> Iterator[str]:
        """生成结束后落盘：xlsx 路由写副本，其余路由不写文件。"""
        if ctx.decision is None or ctx.decision.route not in (
            Route.CASE_XLSX,
            Route.NEW_MODULE,
        ):
            return

        cases = _extract_cases(ctx.response)
        if not cases:
            yield "\n\n[未写入 Excel] 模型输出里没有解析到 JSON 用例数组。"
            return

        try:
            path, added = write_generated_cases(cases, sheet=ctx.sheet or None)
        except Exception as exc:  # noqa: BLE001 - 写文件失败不应吞掉已生成内容
            log.exception("写入 Excel 副本失败")
            yield f"\n\n[写入 Excel 失败] {exc}"
            return

        yield f"\n\n[已写入副本] {path}（+{added} 条，原文件未修改）"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="RAG 流水线：路由 → 检索 → 生成")
    parser.add_argument("--query", required=True, help="用户问题")
    parser.add_argument("--module", default="", help="目标模块，如 采购管理")
    parser.add_argument("--sheet", default="", help="目标工作表，如 采购页")
    parser.add_argument("--dry-run", action="store_true", help="只打印路由、检索与提示词，不调生成")
    args = parser.parse_args(argv)

    pipeline = RAGPipeline()
    if args.dry_run:
        ctx = pipeline.prepare(args.query, module=args.module, sheet=args.sheet)
        print("=" * 80)
        print("route   :", ctx.decision.route.value if ctx.decision else "?")
        print("module  :", ctx.module)
        print("sheet   :", ctx.sheet)
        print("reason  :", ctx.decision.reason if ctx.decision else "")
        if ctx.retrieval:
            print(
                f"hits    : code={len(ctx.retrieval.page_methods)} "
                f"test={len(ctx.retrieval.similar_tests)} "
                f"knowledge={len(ctx.retrieval.business_knowledge)}"
            )
        print("aborted :", ctx.aborted)
        if ctx.aborted:
            print(ctx.abort_message)
        else:
            print("prompt  :", sum(len(m["content"]) for m in ctx.messages), "chars")
        print("=" * 80)
        return 0

    for chunk in pipeline.stream(args.query, module=args.module, sheet=args.sheet):
        print(chunk, end="", flush=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
