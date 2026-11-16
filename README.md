# ERP 测试 Agent 平台

基于 LangChain / LangGraph 的 ERP 测试 Agent 平台。当前主线是四个 Agent：

- **PageAgent**：页面采集与交互；
- **CaseAgent**：测试用例生成；
- **CodeAgent**：根据用例生成 pytest + Playwright 自动化代码；
- **GeneralAgent**：与 ERP 测试无关的普通对话。

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
| `agent/agents.py` | `PageAgent`、`CaseAgent`、`CodeAgent`、`GeneralAgent`、`ERPAgentHub`、轻量意图识别 |
| `agent/orchestrator.py` | `AgentOrchestrator`：`AgentRequest` 路由、执行、GeneralAgent 降级；无性能路由/实体抽取 |
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

目标模块的 `<module>_capture.json` 不存在时，CaseAgent 会立即停止并返回说明；
`web_search` 只用于补充已有控件对应的业务语义，不能替代控件文件。

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

`testself/test_pageagent.py::TestAgentOrchestrator` 会真实调用 LLM + 浏览器，
需要 `.env`、网络和 Chromium，单条约 2~3 分钟。

### 4. 运行 AgentOrchestrator 示例

```bash
python examples/run_agent.py
```

或在异步代码里：

```python
import asyncio

from agent.base import AgentType
from agent.orchestrator import AgentOrchestrator, AgentRequest


async def main() -> None:
    orchestrator = AgentOrchestrator()
    result = await orchestrator.run("生成采购订单的测试用例")
    print(result.agent_type, result.status)
    print(result.content)

    # 显式指定 Agent，跳过意图识别
    result = await orchestrator.run(
        AgentRequest(task="采集采购订单页面的控件", agent_type=AgentType.PAGE)
    )
    print(result.agent_type, result.status)


asyncio.run(main())
```

### 5. 启动 API + 前端

```bash
uvicorn api.main:app --port 8000
```

> Windows 上不要加 `--reload`：Uvicorn 在 reload 子进程模式会切换到
> `SelectorEventLoop`，Playwright 无法通过它创建浏览器子进程，会在
> `create_subprocess_exec` 处报 `NotImplementedError`。需要热更新时，
> 单独重启 API，或只对前端做热更新。

打开：

```text
http://127.0.0.1:8000/
```

页面输入 `sender_id` 和任务，提交后会调用 `POST /api/chat/stream`，以 SSE 增量展示 Agent 输出、Markdown 和 JSON / Excel 产物链接。

控制台包含：对话工作台、知识库管理、用例管理、代码管理、执行管理、会话管理、执行追踪。
当前只有对话工作台接 `/api/chat/stream`，其余模块为前端演示数据/本地状态，待接后端。

`POST /api/chat` 与 `POST /api/chat/stream` 的错误响应：

| 状态码 | 场景 |
|---|---|
| `400` | `task` 为空或只包含空白字符；编排参数不合法 |
| `409` | `message_id` 已存在，重复提交 |
| `422` | 请求体字段缺失或类型不合法（FastAPI 自动校验） |
| `500` | 编排器内部异常 |
| `502` | Agent 执行失败，且 GeneralAgent 兜底也失败；错误记录仍会落库 |
| `503` | 编排器未初始化，或聊天记录数据库不可用 |

成功时返回 `200`；如果目标 Agent 失败但 GeneralAgent 已给出兜底说明，返回 `200` 且 `status="degraded"`。
当前没有服务端超时控制，因此不会主动返回 `504`；长任务超时仍由前端/代理决定。

流式接口在 SSE 开始前可返回 `400/409/422/503`；一旦流已经开始，HTTP 状态固定为 `200`，失败通过 `event: error` 事件返回。

`POST /api/chat/stream` 的事件：

| 事件 | 内容 |
|---|---|
| `meta` | `run_id`、`agent_type`、`route_reason` |
| `delta` | 增量文本 `text` |
| `done` | 最终 `content`、`status`、`artifacts` |
| `error` | 错误说明、`run_id`、部分产物 |

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

### CodeAgent

负责把用例转成自动化测试代码：

- 读取控件 JSON、用例 JSON / xlsx；
- 生成 pytest + Playwright 测试代码；
- 每条用例对应一个 `test_` 函数，包含明确断言；
- 默认输出到 `tests/generated/test_<module>.py`；
- 只生成代码，不执行代码、不修改业务数据。

工具：`read_file`、`write_file`、`edit_file`、`list_dir`、`read_xlsx`。

### GeneralAgent

处理与 ERP 测试无关的问题：

- 闲聊、通用知识、代码解释、写作建议等；
- 直接回答，不调用测试工具；
- 如果用户实际想做 ERP 测试，会提示改成“采集控件”或“生成测试用例”任务。

### 意图识别与路由

- 四类测试意图由 `core.IntentRecognizer` 识别：
  - 拆解需求文档 / PRD → `GeneralAgent`
  - 采集页面控件 / 页面结构 → `PageAgent`
  - 生成测试用例 / Excel → `CaseAgent`
  - 生成 UI 自动化代码 / pytest 脚本 → `CodeAgent`
- 未命中测试场景关键词时，回退 `agent.agents.detect_intent` 的轻量关键词规则；
- 显式传 `AgentRequest(agent_type=...)` 时跳过意图识别；
- 专业 Agent 执行失败时，`AgentOrchestrator` 会降级到 `GeneralAgent` 生成友好说明，`status="degraded"`；不再重放原任务。

## 目录结构

```
RAG_UI_Test/
├── agent/                  # Agent 核心（base/agents/orchestrator/llm/config/loop_logger）
├── tool/                   # LangChain 工具（文件、xlsx、web、校验、context）
├── Skills/                 # page / case_generation / code_generation / general 四份技能
├── testself/               # 当前 pytest 主目录
├── examples/run_agent.py   # AgentOrchestrator 示例
├── frontend/console.html   # Agentest 八模块控制台：对话/知识库/用例/代码/执行/追踪/会话/系统状态
├── frontend/chat.html      # 简单对话页（SSE 流式 + Markdown）
├── frontend/agent_stream.js # SSE 解析与 Markdown 渲染
├── frontend/index.html     # 旧静态原型
├── api/main.py             # FastAPI 入口：/api/chat、/api/chat/stream
├── api/model.py            # ORM 模型
├── api/schema.py           # Pydantic 请求 / 响应
├── core/                   # 意图标签 / 纯规则 IntentRecognizer
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
| `AGENT_MAX_STEPS` | 单任务最大 graph 步数（默认 20，约 10 次工具循环；不是 ReAct 轮数） |
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

- Agent 最大步数由 `AGENT_MAX_STEPS` 控制（默认 20），对应 LangGraph `recursion_limit`；
  这是 graph 步数，不是 ReAct 轮数，20 步约 10 次工具循环。
- 没有独立的 API Agent；`web_fetch` 可以发 HTTP 请求，但没有接口断言工具。
- CaseAgent 在找不到控件时仍可能走 `web_search`，需要在提示词/校验层继续加固。
- 当前 Agent 链路未接入 RAG。
- `/api/chat` 同步等待 Agent 完成；`/api/chat/stream` 已支持 SSE，但暂无服务端超时、断点续传和前端手动停止。
- Agent / Skill 管理接口、鉴权和多用户隔离尚未实现。

## 下一步（方向性）

1. **后端**
   - 补全 `api/`：任务状态轮询、Agent / Skill 管理、日志/产物下载、用例审核；
   - `AgentOrchestrator` 生产化：workspace/artifacts 注入、超时、资源清理、多步骤任务；
   - 工具层继续补强控件发现、路径约定和执行类工具。

2. **前端**
   - 在 `frontend/console.html` 基础上接入知识库、用例、代码、执行、追踪和系统状态接口；
   - 展示 Agent 循环日志、trace、控件 JSON、xlsx 和截图；
   - 支持用例生成结果的人工审核。

> 以上为方向性规划，详细接口与排期以后续 `plan/` 文档为准。

## 历史与兼容

- `infrastructure/` 已删除；不要再参考旧的 POM / 同步 Playwright 写法。
- `DomToExcel/` 是旧工具/应用，保留兼容，不是新 Agent 链路的依赖。
- `docs/`、`plan/`、`docker/`、`knowledge-store/` 中的 RAG 资料是历史资产；RAG 尚未接入当前 Agent。

## 许可证

内部项目，保留所有权利。
