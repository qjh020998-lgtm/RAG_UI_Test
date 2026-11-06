# ERP 测试 Agent 平台

基于 LangChain / LangGraph 的 ERP 测试 Agent 平台。当前主线是两个 Agent：

```
PageAgent（页面交互）                 CaseAgent（用例生成）
  ├─ 异步 Playwright 采集控件/结构  →  artifacts/dom/<module>_capture.json
  ├─ 自动登录、截图、搜索/抓取          ├─ 读取控件 JSON
  └─ 文件读写                          ├─ 生成结构化用例
                                       ├─ validate_cases 校验
                                       └─ json_to_xlsx 写出 xlsx
```

> 旧的 `infrastructure/` 同步 POM / UI 测试驱动已移除，本项目不再依赖它。
> 浏览器自动化统一走 `tool/web.py` 的异步 Playwright。

## 当前已实现（重点）

### Agent 层

| 模块 | 能力 |
|------|------|
| `agent/base.py` | `BaseAgent`：固定 `agent_type` / `system_prompt`，把 `Skills/*.md` 拼进 prompt，交给 LangGraph `create_react_agent` 执行 |
| `agent/agents.py` | `PageAgent`、`CaseAgent`、`ERPAgentHub`、轻量意图识别 |
| `agent/runner.py` | `AgentRunner`：任务分发、执行、结果包装 |
| `agent/llm.py` | `build_llm()`：千问 / DeepSeek 共用 `ChatOpenAI`，支持 fast / strong |
| `agent/loop_logger.py` | 按行记录模型调用、工具调用、token；控制台 + 文件同步输出 |

### 工具层

| 工具 | 作用 |
|------|------|
| `capture_page` | 异步 Playwright 采集 ERP 页面：list / add / all，含登录守卫、截图、JSON 落盘 |
| `close_browser` | 释放采集浏览器 |
| `web_search` / `web_fetch` | 搜索业务语义、抓取网页或调用 HTTP 接口 |
| `read_file` / `write_file` / `edit_file` | 读写控件 JSON、用例 JSON、代码文件 |
| `list_dir` | 列出 workspace 内目录，帮助 Agent 自己发现控件文件 |
| `read_xlsx` / `xlsx_to_json` / `json_to_xlsx` | Excel 读取、JSON 互转、测试用例布局 |
| `validate_cases` | 写 xlsx 前校验字段、编号、步骤、备注、执行情况 |

### 页面采集

- 异步 Playwright，支持 `scenario=list/add/all`；
- 登录态失效时自动登录，账号优先取 `ToolContext.config`，其次取 `.env` 的 `ERP_USERNAME` / `ERP_PASSWORD`；
- 采集结果：
  - `artifacts/dom/<module>_capture.json`（总汇总）
  - `artifacts/dom/<module>_facts_compact.json`
  - `artifacts/dom/<module>_list.json`
  - `artifacts/dom/<module>_add.json`
  - `artifacts/screenshots/<module>_list.png` / `_add.png`

### 用例生成

1. `list_dir("artifacts/dom")` 找到 `<module>_capture.json`；
2. `read_file` 读取控件字段、表格列、弹窗和下拉选项；
3. 生成用例 JSON；
4. `validate_cases` 校验，直到 `ok=true`；
5. `json_to_xlsx` 写出 xlsx。

控件文件找不到时才允许 `web_search` 补充业务语义作为兜底。

### 日志

- 每次运行独立文件：`reports/logs/test_run_<YYYYmmdd_HHMMSS>_<pid>.log`；
- Agent 循环日志形如：

```text
[PageAgent] task start | 采集采购订单页面的控件
[PageAgent] model#1 start | messages=2 | last=human:...
[PageAgent] model#1 end | finish=tool_calls | tool_calls=capture_page | usage=in=2805 out=23
[PageAgent] tool#1 start | capture_page | args={...}
[PageAgent] tool end | capture_page | result=...
[PageAgent] model#2 end | final | ...
[PageAgent] task done | 12.3s | messages=4
```

- 开启 `LANGSMITH_TRACING=true` 后，云端还可查看完整 model/tool trace。

## 快速开始

### 1. 安装

```bash
uv sync
playwright install chromium
```

### 2. 配置 `.env`

```dotenv
LLM_API_KEY=...
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FAST_MODEL=qwen3.8-flash
LLM_STRONG_MODEL=qwen3.8-max

ERP_USERNAME=jsh
ERP_PASSWORD=123456

# 可选：LangSmith
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=project_api
```

### 3. 运行测试

```bash
# 当前主测试目录：testself/
pytest
pytest --collect-only -q

# 单个模块
pytest testself/test_case_validator.py -q
pytest testself/test_xlsx_handler.py -q
```

`testself/test_pageagent.py::TestAgentRunner` 会真实调用 LLM + 浏览器，
需要 `.env`、网络和 Chromium，单条约 2~3 分钟。

### 4. 运行 AgentRunner 示例

```bash
python examples/run_agent.py
```

或在异步代码里：

```python
import asyncio

from agent.runner import AgentRunner


async def main() -> None:
    runner = AgentRunner()
    result = await runner.run("生成采购订单的测试用例")
    print(result["agent_type"], result["status"])
    print(result["content"])


asyncio.run(main())
```

## Agent 说明

### PageAgent

负责与外部系统交互：

- 采集控件与页面结构；
- 自动登录 ERP；
- 调用 HTTP / 抓取网页；
- 读写文件。

工具：`capture_page`、`close_browser`、`web_fetch`、`web_search`、`read_file`、`write_file`、`edit_file`。

### CaseAgent

负责数据处理：

- 读取控件 JSON；
- 生成测试用例；
- 校验用例；
- 写 xlsx。

工具：`read_file`、`write_file`、`edit_file`、`list_dir`、`read_xlsx`、`xlsx_to_json`、`json_to_xlsx`、`validate_cases`、`web_search`。

### 意图识别

- 包含“用例 / 测试点 / 测试场景 / 测试设计 / 生成测试” → `CaseAgent`
- 其他任务 → `PageAgent`

## 目录结构

```
RAG_UI_Test/
├── agent/                  # Agent 核心（base/agents/runner/llm/config/loop_logger）
├── tool/                   # LangChain 工具（文件、xlsx、web、校验、context）
├── Skills/                 # page / case_generation 两份技能
├── testself/               # 当前 pytest 主目录
├── examples/run_agent.py   # AgentRunner 示例
├── frontend/index.html     # 前端静态原型（尚未接后端）
├── api/                    # 后端 API 占位（未实现）
├── core/                   # 核心占位（未实现）
├── data/                   # YAML 数据
├── reports/logs/           # 每次运行独立日志
├── artifacts/              # 运行时产物：dom / screenshots / xlsx
├── plan/                   # 历史设计与阶段总结
└── DomToExcel/             # 旧工具/应用（legacy）
```

## 配置说明

| 变量 | 用途 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` | 模型鉴权与地址 |
| `LLM_FAST_MODEL` / `LLM_STRONG_MODEL` | 快/强模型 |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_TIMEOUT` / `LLM_MAX_RETRIES` | 生成参数 |
| `ERP_USERNAME` / `ERP_PASSWORD` | 登录态失效时自动登录 |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | 可选云端 trace |

工具运行目录由 `ToolContext` 控制：

```python
from tool.context import configure

configure(
    workspace="D:/RAG/RAG_UI_Test",
    artifacts="D:/RAG/RAG_UI_Test/artifacts",
    config={"headless": True},
)
```

默认 `workspace` 是进程 CWD；相对路径都以它为基准。

## 已知限制

- `AgentRunner.max_turns` 目前只保存，未传给 graph；轮数仍由 LangGraph 默认值控制。
- 没有独立的 API Agent；`web_fetch` 可以发 HTTP 请求，但没有接口断言工具。
- CaseAgent 在找不到控件时仍可能走 `web_search`，需要在提示词/校验层继续加固。
- 当前 Agent 链路未接入 RAG。
- 后端 API 与前端交互尚未实现，`frontend/index.html` 只是静态原型。

## 下一步（方向性）

1. **后端**
   - 实现 `api/`：任务提交、状态查询、日志/产物下载、用例审核；
   - `AgentRunner` 生产化：workspace/artifacts 注入、超时、资源清理、多步骤任务；
   - 工具层继续补强控件发现、路径约定和执行类工具。

2. **前端**
   - 将 `frontend/index.html` 接入后端 API；
   - 展示 Agent 循环日志、trace、控件 JSON、xlsx 和截图；
   - 支持用例生成结果的人工审核。

> 以上为方向性规划，详细接口与排期以后续 `plan/` 文档为准。

## 历史与兼容

- `infrastructure/` 已删除；不要再参考旧的 POM / 同步 Playwright 写法。
- `DomToExcel/` 是旧工具/应用，保留兼容，不是新 Agent 链路的依赖。
- `docs/`、`plan/`、`docker/`、`knowledge-store/` 中的 RAG 资料是历史资产；RAG 尚未接入当前 Agent。

## 许可证

内部项目，保留所有权利。
