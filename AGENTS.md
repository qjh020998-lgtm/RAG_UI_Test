# AGENTS.md

给 AI 编码助手（Codex / Claude Code / Cursor 等）的项目说明。动手改代码前先读完本文，
它记录了项目边界、硬性约定和当前进度；细节设计与路线图见 `plan/`。

---

## 1. 项目是什么

面向自研 ERP 系统的智能 UI 自动化测试框架，最终形态 = **POM + RAG 知识库 + LLM 辅助**：

1. **POM 自动化**（已完成）：Playwright + Pytest，基于 Accessibility Tree 定位。
2. **RAG 知识库**（未开始）：业务文档 → 切片 → BGE-M3（dense + sparse）向量化 → Milvus 检索。
3. **AIGC 用例生成**（未开始）：RAG 检索 + Page 类签名作为上下文，LLM 生成 pytest 代码。
4. **定位自愈**（未开始）：定位失败时多策略回退 + LLM 分析 A11y Tree。

当前被测试系统：ERP 登录页 `http://39.101.122.147:3000/user/login`。

## 2. 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 基础设施层（tools/playwright、page、data、testcases） | ✅ 已完成 |
| Phase 2 | RAG 知识库（BGE-M3 + Milvus，见 `plan/think or choice/rag_retrieval_design.md`） | 📐 设计完成，待编码 |
| Phase 3 | AIGC 测试用例生成 | ⏳ 未开始 |
| Phase 4 | 控件定位自愈 | ⏳ 未开始 |
| Phase 5 | 监控与报告 | ⏳ 未开始 |

> `agent/` 下的 8 个文件目前是**空占位**（0 字节），是 Phase 2-5 的骨架，
> 属于「未完成的项目架构」，不要因为文件为空就删除它们。

## 3. 目录结构与职责

```
RAG_UI_Test/
├── agent/                  # AI Agent 层（占位，Phase 2-5 实现）
│   ├── app.py              # Agent 应用入口
│   ├── config.py           # Agent 配置
│   ├── model.py            # LLM 模型管理
│   ├── rag_pipline.py      # RAG 流水线
│   ├── schema.py           # 数据模型
│   ├── security.py         # 安全相关
│   └── monitering.py       # 监控
├── tools/playwright/       # Playwright 封装（不写业务）
│   ├── conf.py             # 配置（Pydantic-Settings，PW_ 前缀）
│   ├── locators.py         # A11y 定位器（8 种语义定位）
│   ├── actions.py          # 浏览器操作（点击/填写/等待/断言/截图）
│   └── browser.py          # 浏览器生命周期管理
├── page/                   # Page Object 层（一个页面一个类）
│   ├── base_page.py        # 页面基类
│   └── login_page.py       # 登录页
├── data/                   # 只放 YAML 测试数据，禁止放 .py
│   ├── config.yaml         # base_url 等运行配置
│   └── login_cases.yaml    # 登录测试数据
├── data_loader.py          # YAML 加载 + Pydantic 校验（唯一数据入口）
├── testcases/
│   ├── conftest.py         # 浏览器/page fixture
│   └── test_login.py       # 登录用例（parametrize 数据驱动）
├── artifacts/              # 运行时产物：截图/trace/下载（git 忽略，自动创建）
├── docs/                   # 业务文档（RAG 知识库来源，待填充）
├── cache/                  # 文档处理产物（chunks.json、OCR 缓存等，git 忽略）
├── knowledge-store/        # 知识库产物（导出/评测/备份；Milvus 数据在 Docker volume）
├── plan/                   # 项目计划与阶段总结（设计依据）
│   ├── initial_project_plan_2026-10-17.md
│   └── think or choice/    # 选型分析与设计决策文档
├── .codex/skills/pom-ui-automation-generator/SKILL.md  # POM 代码生成规范
├── main.py                 # 入口示例（仅打印帮助）
├── pyproject.toml          # 依赖（uv 管理）
├── pytest.ini              # pytest 配置与 markers
└── README.md               # 用户向使用文档
```

## 4. 硬性约定（改代码必须遵守）

### 4.1 数据层

- **`data/` 只允许 `.yaml`**。加载/校验逻辑一律放根目录 `data_loader.py`，不要往 `data/` 加 `__init__.py`、脚本或截图目录。
- 新增数据文件：`data/xxx.yaml` + 在 `data_loader.py` 定义 Pydantic 模型 + 加载函数。
- 运行时产物（截图、trace、下载）写 `artifacts/`，不写 `data/`。
- 测试数据里不放真实生产凭证；账号来自 `data/login_cases.yaml`。

### 4.2 测试层

- **所有测试参数必须通过 `@pytest.mark.parametrize` 装饰器传入**，不要在测试函数体里硬编码数据、也不要新增「读 YAML 再循环」的写法。
- 数据在模块级加载一次（`LOGIN_CASES = load_login_cases()`），`parametrize` 用 `ids=` 指定可读用例名。
- 用例只调用 Page Object 的方法，不直接写 `page.locator(...)`；数据只通过 `data_loader` 获取。
- 测试文件命名 `test_*.py`，测试类 `Test*`，测试函数 `test_*`；标记只用 `smoke` / `regression` / `e2e`（`pytest.ini` 已声明，`--strict-markers` 生效）。
- 每个用例独立上下文（`context`/`page` fixture 是 function 级），保证隔离。

模板：

```python
import pytest

from data_loader import load_login_cases
from page.login_page import LoginPage

LOGIN_CASES = load_login_cases()


class TestLogin:
    @pytest.mark.parametrize(
        "case",
        LOGIN_CASES,
        ids=[case.name for case in LOGIN_CASES],
    )
    def test_login(self, login_page: LoginPage, case) -> None:
        login_page.goto_login()
        login_page.login_and_wait(
            case.username,
            case.password,
            expect_success=case.expect_success,
        )
        if case.expect_success:
            login_page.assert_login_success()
        else:
            login_page.assert_login_error()
```

### 4.3 定位与 Page Object

- 优先 Accessibility Tree 语义定位：`get_by_role` / `get_by_label` / `get_by_placeholder` / `get_by_text`。
- 避免 CSS/XPath（如 `#submit`、`div.form`）——即使当前登录页因为历史原因用了 `#loginName` 这类 id，新增页面不要效仿。
- 元素用 `@property` 懒加载；业务操作封装成方法；断言方法以 `assert_` 开头。
- 新增页面：`page/<name>_page.py` 继承 `BasePage`，并在 `page/__init__.py` 导出。

### 4.4 配置

- 优先用 `PlaywrightConfig`（`PW_` 前缀环境变量），例如 `PW_BROWSER_TYPE`、`PW_HEADLESS`、`PW_SLOW_MO`、`PW_TRACE_ENABLED`。
- `base_url` 来自 `data/config.yaml`，由 `data_loader.load_config()` 读取，不要在用例里写死 URL。
- 不要提交 `.venv/`、`.idea/`、`artifacts/`、`cache/`、缓存目录。



### 4.5 RAG 层

- **嵌入模型**：BGE-M3（路径 `D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master`），使用 FlagEmbedding SDK。
- **向量库**：Milvus standalone（Docker Compose，`localhost:19530`），collection 名 `erp_knowledge`。
- **检索策略**：dense + sparse 双路召回 + RRFRanker(k=60) 融合，支持 metadata 过滤（module、chunk_type）。
- **数据摄入**：从 `cache/_all_chunks.json` 读取已切片数据，按 chunk_type 分类构造 embedding_text 后批量嵌入写入 Milvus。
- **配置前缀**：`RAG_`（如 `RAG_MILVUS_URI`、`RAG_COLLECTION_NAME`）。
- 不要把 chunks 数据或嵌入结果放到 `data/` 目录；中间产物放 `cache/`，知识库产出放 `knowledge-store/`。

## 5. 常用命令

```bash
# 安装依赖（uv 或 pip）
uv sync
playwright install chromium

# 运行全部测试
pytest

# 冒烟 / 回归 / 端到端
pytest -m smoke
pytest -m regression
pytest -m e2e

# 只看参数化展开结果（不启动浏览器）
pytest --collect-only -q

# 调试：显示浏览器窗口、放慢操作
PW_HEADLESS=false PW_SLOW_MO=500 pytest testcases/test_login.py -v -s

# 框架入口帮助
python main.py

# Milvus 相关
docker compose up -d              # 启动 Milvus（etcd + MinIO + milvus-standalone）
docker compose down               # 停止 Milvus

# RAG 数据摄入（待实现）
python -m agent.rag_pipline --ingest cache/_all_chunks.json

# RAG 检索测试（待实现）
python -m agent.rag_pipline --query "采购入库的操作流程"
```

## 6. 数据文件格式

`data/login_cases.yaml`（**YAML 列表**，每项一条用例）：

```yaml
- name: valid_default          # 用例 ID，会显示在 pytest 输出里
  username: "jsh"
  password: "123456"
  expect_success: true         # true: 登录后离开 /user/login；false: 停留
```

`data/config.yaml`：

```yaml
base_url: "http://39.101.122.147:3000"
```

对应模型在 `data_loader.py`：`LoginCase`、`FrameworkConfig`。新增字段先改模型再改 YAML。

## 7. 架构决策（来自 plan/，不要轻易推翻）

1. **定位策略**：Accessibility Tree 优先于 DOM，抗前端重构、语义化、Playwright 原生支持。
2. **LLM 策略**：Qwen 3.8 双模型——`qwen3.8-flash` 处理常规任务，`qwen3.8-max` 负责代码生成与自愈。
3. **嵌入模型**：本地 BGE-M3，路径 `D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master`。
4. **向量库**：**Milvus 单机版（Docker Compose：etcd + MinIO + milvus-standalone，19530/9091）**，dense + sparse 双路召回、RRFRanker 融合；按 `doc_version` 分区 + collection alias 切换，方案见 `plan/think or choice/rag_retrieval_design.md`。
5. **AIGC 采用混合架构**（非纯 Agentic）：RAG 检索 + 测试规划 → 自动收集 Page 类签名 → LLM 按模板生成 → AST 校验后写入 `testcases/`。理由：兼顾精度、延迟与 token 成本。

## 8. 修改时的注意事项

- 不要删除 `agent/`、`docs/`、`knowledge-store/`、`plan/`、`.codex/`：它们是未完成模块和设计依据。
- 不要往 `data/` 放非 YAML 文件；不要恢复 `data_loader.py` 到 `data/` 里。
- 不要绕过 `parametrize` 传参；不要在测试里重复登录/硬编码 URL 或凭证。
- 改动 fixture 或 `PlaywrightConfig` 默认值时，同步更新 README 和本文件。
- 新增依赖请写进 `pyproject.toml`，并保持 `uv.lock` 同步。

## 9. 参考文档

- `plan/initial_project_plan_2026-10-17.md`：完整架构、RAG/LLM/自愈设计与路线图
- `plan/phase1_implementation_summary.md`：Phase 1 实施清单
- `plan/think or choice/rag_retrieval_design.md`：Phase 2 检索层选型分析（三种方案对比、Milvus schema、嵌入策略、实施计划）
- `.codex/skills/pom-ui-automation-generator/SKILL.md`：POM 代码生成规范
- `README.md`：面向使用者的快速开始
