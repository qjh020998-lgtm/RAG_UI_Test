# AGENTS.md

给 AI 编码助手（Codex / Claude Code / Cursor 等）的项目说明。动手改代码前先读完本文。
本文已按「Agent 测试平台」重写；`plan/`、`docs/` 中的旧 POM/RAG 文档仅作历史参考。

---

## 1. 项目定位

面向自研 ERP 系统的 **LLM Agent 测试平台**。当前主线是：

```
PageAgent 采集页面控件/结构  →  CaseAgent 生成、校验并写出测试用例
        （异步 Playwright）              （文件 + Excel）
```

- Agent 框架：LangChain 1.x + LangGraph `create_react_agent`
- 模型：`ChatOpenAI`，兼容千问（DashScope）与 DeepSeek
- 浏览器：`async_playwright`（**不是**旧的同步 POM 驱动）
- 测试：pytest，当前 testpaths 为 `testself/`
- 正在规划：后端 API + 前端交互界面

> `infrastructure/`（旧同步 POM / UI 测试 / conftest 浏览器 fixture）已删除，
> 不要再新增或恢复 `sync_playwright` 的 UI 测试栈。

## 2. 当前已实现（重点）

| 模块 | 状态 | 说明 |
|------|------|------|
| `agent/base.py` | ✅ | `BaseAgent`：固定 `agent_type` / `system_prompt`，把 `Skills/*.md` 拼进 prompt，用 `create_react_agent` 执行，带 `AgentLoopLogger` |
| `agent/agents.py` | ✅ | `PageAgent`、`CaseAgent`、`ERPAgentHub`、轻量意图识别 |
| `agent/runner.py` | ✅ | `AgentRunner.run` / `run_with_agent_type`，统一返回 `{agent_type, content, status}` |
| `agent/llm.py` | ✅ | `build_llm()`：按 `agent/config.py` 创建千问 / DeepSeek 的 `ChatOpenAI`，支持 fast / strong |
| `agent/loop_logger.py` | ✅ | LangChain callback：按行记录 model/tool/token，控制台与文件输出 |
| `tool/` | ✅ | LangChain 工具：文件、目录、xlsx、网页/ERP 采集、用例校验 |
| `Skills/` | ✅ | 两份技能：`page/SKILL.md`、`case_generation/SKILL.md` |
| `testself/` | ✅ | 工具/Agent 自测；含真实 AgentRunner 集成测试 |
| 页面采集 | ✅ | `capture_page`：list/add/all，自动登录，截图与 JSON 落 `artifacts/` |
| 用例生成 | ✅ | 读取控件 JSON → 生成用例 → `validate_cases` → `json_to_xlsx`；找不到控件时允许 `web_search` 兜底 |
| 运行日志 | ✅ | 每次进程独立 `reports/logs/test_run_<timestamp>_<pid>.log` |
| `frontend/index.html` | 🚧 | 静态界面原型，尚未接后端 |
| `api/`、`core/` | 🚧 | 空占位，后端尚未实现 |
| RAG 检索 | ⏸️ | 当前 Agent 链路**未接入** RAG；`docs/`、`docker/`、`knowledge-store/` 保留为后续资产 |
| `DomToExcel/` | 🗄️ | 旧工具/应用，保留兼容，不是新 Agent 的依赖 |

### 已知限制

- `AgentRunner.max_turns` 目前只保存、未传给 graph，轮数仍由 LangGraph 默认值控制。
- CaseAgent 没有强制的“必须读控件”门禁；控件找不到时会用 `web_search`。
- 没有 API Agent；`web_fetch` 可发 HTTP，但没有专门的接口断言工具。
- 没有后端 API，也没有真正的 Web 前端；`frontend/index.html` 只是静态原型。

## 3. 目录结构

```
RAG_UI_Test/
├── agent/                  # Agent 核心
│   ├── base.py             # BaseAgent + Skills 组装 + graph 调用
│   ├── agents.py           # PageAgent / CaseAgent / ERPAgentHub
│   ├── runner.py           # AgentRunner 统一入口
│   ├── llm.py              # ChatOpenAI 工厂（千问 / DeepSeek）
│   ├── config.py           # LLM 配置读取
│   └── loop_logger.py      # Agent 循环日志 callback
├── tool/                   # LangChain 工具层
│   ├── context.py          # ToolContext / configure / get_context
│   ├── file_system.py      # read_file / write_file / edit_file / list_dir
│   ├── xlsx_handler.py     # read_xlsx / xlsx_to_json / json_to_xlsx
│   ├── web.py              # capture_page / web_search / web_fetch / close_browser
│   └── case_validator.py   # validate_cases
├── Skills/
│   ├── page/SKILL.md
│   └── case_generation/SKILL.md
├── testself/               # 当前 pytest 主目录（testpaths）
├── examples/run_agent.py   # AgentRunner 运行示例
├── frontend/index.html     # 前端静态原型
├── api/                    # 后端 API 占位（未实现）
├── core/                   # 核心占位（未实现）
├── data/                   # YAML 数据（config / login_cases / purchase）
├── docs/                   # 业务文档（RAG 历史资产）
├── reports/logs/           # 每次运行独立日志
├── artifacts/              # 运行时产物：dom / screenshots / xlsx
├── plan/                   # 历史设计与阶段总结
└── DomToExcel/             # 旧工具/应用（legacy）
```

## 4. 硬性约定

### 4.1 Agent 边界

- **PageAgent** 只做外部交互：`capture_page`、`close_browser`、`web_fetch`、`web_search`、文件读写。
- **CaseAgent** 只做数据处理：读控件 JSON、生成用例、`validate_cases`、写 xlsx。
- 工具通过各自的 `default_tools()` 分配，不要在调用点临时塞工具；新增工具先判断属于哪一层。
- 不要再引入 `infrastructure.*` 或旧的 `page/`、`pw_core/` POM 代码。

### 4.2 异步约定

- Agent / 工具主链路全部 async；`capture_page`、`web_search`、`web_fetch`、`close_browser` 都是 `async def`。
- 不要在运行中的事件循环里调用同步 Playwright，也不要把 `sync_playwright` 带回项目。
- `AgentRunner` 必须在同一个事件循环里 `await`；同步脚本用 `asyncio.run(main())`，不要在 async 函数里再 `asyncio.run`。
- 不要在已有同步 Playwright 的线程里调 `asyncio.run`。

### 4.3 ToolContext 与路径

- 工具统一通过 `get_context()` 读取 `ToolContext`；启动时用 `configure(workspace=..., artifacts=..., config=...)` 注入。
- 默认 `workspace` 是进程启动时的 CWD，默认 `artifacts` 是 `workspace/artifacts/<run_id>`。
- 工具的相对路径都基于 `workspace`；任务里不要重复拼 `testself/selfcache/...` 这类前缀，否则会路径重复。
- CaseAgent 路径不确定时先用 `list_dir(".")` / `list_dir("artifacts/dom")` 发现文件，再 `read_file`。
- 控件文件约定：`artifacts/dom/<module>_capture.json`、`_facts_compact.json`、`_list.json`、`_add.json`。

### 4.4 工具写法

- 使用 `@tool(args_schema=PydanticModel)`；返回字符串，错误统一 `"错误：类型: 信息"`，不要把异常直接抛给 Agent（登录失败除外）。
- `read_file` / `list_dir` 必须限制在 `workspace` 内；`list_dir` 只列一层、默认最多 200 条。
- `capture_page` 的登录守卫会抛 `LoginError`，捕获后关闭 session、清空 `ctx.browser`，并返回 `错误：登录失败：...`。
- 同步工具（文件/xlsx/校验）由 LangChain `ainvoke` 自动放线程池，不要手动 `asyncio.to_thread`。

### 4.5 日志

- 每次运行独立文件：`reports/logs/test_run_YYYYmmdd_HHMMSS_<pid>.log`；不要再写死 `test_run.log`。
- Agent 循环用 `AgentLoopLogger` 记录 model/tool/token，不要打开 `langchain` / `langgraph` 的 DEBUG 日志。
- `pytest_assertion_pass` 只记录项目断言；`agent` marker 测试和 anyio/langsmith 内部断言已过滤。
- 日志里不要输出完整 prompt、账号密码、token；参数/结果截断到 300 字符左右。

### 4.6 测试

- `pytest.ini` 的 `testpaths = testself`；运行 `pytest` 即跑当前主线测试。
- marker：`smoke` / `regression` / `e2e` / `agent`（`--strict-markers`）。
- `testself/test_pageagent.py` 的 `TestAgentRunner` 会真实调用 LLM + 浏览器，需要 `.env`、网络和 `playwright install chromium`，单条约 2~3 分钟。
- 测试产物写 `testself/selfcache/` 或 pytest `tmp_path`，不要污染 `data/`、`cache/`、`knowledge-store/`。
- 不要为了修路径去改用户的任务文本；应在 Agent 侧用 `list_dir` / 技能描述解决。

### 4.7 依赖与密钥

- 新增依赖写 `pyproject.toml`，并同步 `uv.lock`。
- `.env` 含密钥，已 git 忽略；常用键：
  `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_FAST_MODEL`、`LLM_STRONG_MODEL`、
  `ERP_USERNAME`、`ERP_PASSWORD`、`LANGSMITH_API_KEY`、`LANGSMITH_TRACING`。
- `DomToExcel/` 是旧实现，不要从 `agent/` 或 `tool/` 反向依赖它。

## 5. 常用命令

```bash
# 依赖 / 浏览器
uv sync
playwright install chromium

# 当前主测试目录（pytest.ini testpaths=testself）
pytest
pytest testself/test_pageagent.py -q
pytest testself/test_pageagent.py::TestAgentRunner::test_runner_execute_case_task -q -s
pytest --collect-only -q

# AgentRunner 示例
python examples/run_agent.py

# 日志
# reports/logs/test_run_<timestamp>_<pid>.log
# 每次进程独立文件，不会覆盖历史日志
```

## 6. 下一步（方向性，未定稿）

1. **后端**
   - 实现 `api/`：任务提交、状态查询、日志/产物下载、用例审核接口；
   - `AgentRunner` 生产化：workspace/artifacts 注入、超时、资源清理、多步骤 pipeline；
   - 工具层补齐：路径约定、控件索引/发现、必要的执行类工具；
   - 视需要接入 RAG，并评估 API Agent。

2. **前端**
   - 把 `frontend/index.html` 接到后端 API；
   - 任务输入、Agent 循环日志/ LangSmith trace、产物（控件 JSON / xlsx / 截图）展示；
   - 用例生成结果的人工审核与回写。

> 以上是方向，不是承诺的详细路线图；具体排期与接口设计以后续 `plan/` 文档为准。

## 7. 参考

- `examples/README.md`：AgentRunner 用法
- `Skills/page/SKILL.md`、`Skills/case_generation/SKILL.md`：Agent 行为规范
- `testself/test_pageagent.py`：当前端到端测试
- `plan/`、`docs/`：历史设计与 RAG 资料（部分已过时）
- `DomToExcel/`：旧工具/应用，仅兼容保留
