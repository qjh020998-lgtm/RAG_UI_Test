# AGENTS.md

给 AI 编码助手（Codex / Claude Code / Cursor 等）的项目说明。动手改代码前先读完本文，
它记录了项目边界、硬性约定和当前进度；细节设计与路线图见 `plan/`。

---

## 1. 项目是什么

面向自研 ERP 系统的智能 UI 自动化测试框架，最终形态 = **POM + RAG 知识库 + LLM 辅助**：

1. **POM 自动化**（已完成）：Playwright + Pytest，基于 Accessibility Tree 定位。
2. **RAG 知识库**（主链路已跑通，Phase 2.5 已加固）：业务文档 → 切片 → BGE-M3（dense + sparse）向量化 → Milvus 检索。
3. **AIGC 用例生成**（未开始，选型已定）：RAG 检索 + Page 类签名作为上下文，LLM 生成 pytest 代码。
4. **定位自愈**（未开始）：定位失败时多策略回退 + LLM 分析 A11y Tree。

当前被测试系统：ERP 登录页 `http://39.101.122.147:3000/user/login`。

## 2. 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 基础设施层（pw_core、page、data、testcases） | ✅ 已完成 |
| Phase 2 | RAG 知识库（文档切分 + OCR + BGE-M3 + Milvus 混合检索） | ✅ 主链路已跑通（库内 235 条，检索实测通过） |
| Phase 2.5 | RAG 数据质量与工程加固（见 `plan/phase2_alignment_2026-10-19.md`） | ✅ 已完成：位置型主键、幂等 ingest、OCR 修复、dense 门控、manifest |
| Phase 3 | AIGC 测试用例生成（选型见 `plan/think or choice/aigc_code_generation_strategy.md`） | 🚧 Phase 3.0 基础设施已完成（pw_core/BasePage/Driver/契约测试），3.1+ 待做 |
| Phase 4 | 控件定位自愈 | ⏳ 未开始 |
| Phase 5 | 监控与报告 | ⏳ 未开始 |

**实测状态（2026-10-19，Phase 2.5 重建后）**：collection `erp_knowledge` 有
**235 条**实体（text 103 / image 102 / table 30），主键为位置型
`{doc_slug}_{txt|img|tbl}_pXXX_iXXXX`，`content_hash` 为普通字段；dense 索引
`IVF_FLAT+COSINE(nlist=128)`、sparse 索引 `SPARSE_INVERTED_INDEX+IP`。
稳态单查询 **245~294ms**（进程级 BGE-M3 单例），无关查询 dense 门控后返回空。
`knowledge-store/manifests/` 记录每次 ingest；`doc_version` 分区与 alias **尚未实现**。

> `agent/` 下仍有 6 个**空占位**文件（0 字节）：`app.py`、`cache.py`、`models.py`、
> `monitering.py`、`rag_pipline.py`、`security.py`，是 Phase 2.5-5 的骨架，
> 属于「未完成的项目架构」，不要因为文件为空就删除它们。
> 已实现的是：`config.py`、`schema.py`、`embedding.py`、`load_documents.py`。
> 注意原 `model.py` 已重命名为 `models.py`（仍为空）。

## 3. 目录结构与职责

```
RAG_UI_Test/
├── agent/                  # AI Agent 层（RAG 部分已实现，其余为 Phase 3-5 占位）
│   ├── config.py           # ✅ RAG 配置（RAG_ 前缀，Milvus/Embedding/LLM/Search）
│   ├── schema.py           # ✅ Milvus collection schema + ChunkRecord + 索引管理
│   ├── embedding.py        # ✅ BGE-M3 封装 + build_embedding_text + ingest + hybrid_search
│   ├── load_documents.py   # ✅ 文档加载/切分统一入口（python -m agent.load_documents）
│   ├── app.py              # ⏳ 空占位：Agent 应用入口
│   ├── models.py           # ⏳ 空占位：LLM 模型管理（原 model.py 已改名）
│   ├── rag_pipline.py      # ⏳ 空占位：RAG 流水线（Phase 3 落地 RAGPipeline）
│   ├── cache.py            # ⏳ 空占位：缓存层
│   ├── security.py         # ⏳ 空占位：安全相关
│   └── monitering.py       # ⏳ 空占位：监控
├── tools/document/         # ✅ 文档处理层（Phase 2 新增，不写业务）
│   ├── schema.py           # DocumentChunk / ChunkMetadata / ImageMetadata / generate_chunk_id
│   ├── md_filtter.py       # Markdown 解析切分（按 H2/H3）
│   ├── pdf_filtter.py      # PDF 解析切分（按 ToC 小节）+ 图片提取 + RapidOCR + OCR 缓存
│   └── __init__.py         # DocumentProcessor 统一入口
├── pw_core/                # ✅ Playwright 核心包（不写业务）
│   ├── conf.py             # 配置（PW_ 前缀；ApiConfig 用 PW_API_）
│   ├── base_page.py        # 页面基类：定位/动作/等待/断言/弹窗/表格
│   ├── base_test.py        # BaseTest + BasePage 契约检查
│   └── driver/             # Web/API 驱动层
│       ├── driver_factory.py  # 工厂：按平台创建驱动
│       ├── driver_manager.py  # 管理器：API vs Web 策略
│       ├── web_driver.py      # Playwright 浏览器/context/page 生命周期
│       └── api_client.py      # 可选 ERP API 客户端（httpx）
├── tools/view_milvus_data.py  # ✅ Milvus 数据浏览/检索演示工具
├── page/                   # Page Object 层（一个页面一个类）
│   ├── login_page.py       # 登录页
│   ├── purchase_page.py    # 采购管理列表页/新增弹窗
│   └── retail_page.py      # 零售管理列表页/新增弹窗
├── data/                   # 只放 YAML 测试数据，禁止放 .py
│   ├── config.yaml         # base_url 等运行配置
│   └── login_cases.yaml    # 登录测试数据
├── data_loader.py          # YAML 加载 + Pydantic 校验（唯一数据入口）
├── testcases/
│   └── test_login.py       # 登录用例（parametrize 数据驱动）
├── conftest.py             # ✅ 根级浏览器/context/page/auth fixture
├── testself/               # 框架自测（文档处理 + 嵌入检索质量）；不在 pytest.ini 的 testpaths 内，需显式指定路径
├── docs/                   # ✅ 业务文档（RAG 知识库来源，已填充 3 MD + 1 PDF）
├── docker-compose.yml      # ✅ Milvus 部署（唯一权威：v2.5.14，数据落 ./volumes/）
├── docker/README.md        # ✅ Docker 说明（旧 milvus compose 已删除，根目录为唯一权威）
├── volumes/                # Milvus/etcd/MinIO 数据目录（git 忽略）
├── .env                    # 环境配置（含密钥，git 忽略；Milvus / BGE-M3 / LLM / 检索参数）
├── artifacts/              # 运行时产物：截图/trace/下载（git 忽略，自动创建）
├── cache/                  # 文档处理产物（chunks.json、ocr_cache.json，git 忽略）
├── knowledge-store/        # 知识库产物（images/、导出/评测/备份；git 忽略）
├── plan/                   # 项目计划与阶段总结（设计依据）
│   ├── initial_project_plan_2026-10-17.md
│   ├── phase2_alignment_2026-10-19.md   # 进度对齐 + 缺陷清单 + 下一步计划
│   └── think or choice/    # 选型分析与设计决策文档
├── .codex/skills/pom-ui-automation-generator/SKILL.md  # POM 代码生成规范
├── .codex/skills/rag-knowledge-base/SKILL.md             # RAG 知识库规范（references/ 待填充）
├── main.py                 # 入口示例（仅打印帮助）
├── run_tests.py            # pytest 启动脚本
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
from infrastructure.page.login_page import LoginPage

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
- **关联单据统一用 `BasePage.open_link_search(label_keyword="关联")` + `get_link_modal()`**。
  采购订单 label 是“关联请购单”、采购入库是“关联订单”、采购退货是“关联单据”，但 DOM 同构：
  readonly input + `span.ant-input-suffix > i.anticon-search`；搜索图标不是 button，不能 fill 输入框。
- **A11y 缓存里的 `button "图标: search"` 不是 Playwright button**：实际是
  `<i class="anticon-search">`，必须用 `i.anticon-xxx`；禁止生成
  `get_by_role('button', name='图标: ...')`。
- 商品选择弹窗的关键词输入是 textbox（placeholder 含“条码/名称/关键词”），不是 combobox；
  统一用 `fill_product_search_keyword()`（内部填词 + 点查询）。
- 请购单没有仓库字段：`select_warehouse()` 在 `/bill/purchase_apply` 直接跳过，只有采购入库/退货才选仓库。

### 4.4 配置

- 优先用 `PlaywrightConfig`（`PW_` 前缀环境变量），例如 `PW_BROWSER_TYPE`、`PW_HEADLESS`、`PW_SLOW_MO`、`PW_TRACE_ENABLED`。
- `base_url` 来自 `data/config.yaml`，由 `data_loader.load_config()` 读取，不要在用例里写死 URL。
- 不要提交 `.venv/`、`.idea/`、`artifacts/`、`cache/`、缓存目录。



### 4.5 RAG 层

- **嵌入模型**：BGE-M3（路径 `D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master`），使用 FlagEmbedding SDK。
- **向量库**：Milvus standalone（Docker Compose，`localhost:19530`），collection 名 `erp_knowledge`。
- **检索策略**：dense + sparse 双路召回 + RRFRanker(k=60) 融合，支持 metadata 过滤（module、chunk_type）。
- **数据摄入**：从 `cache/chunks.json` 读取已切片数据，按 chunk_type 分类构造 embedding_text 后批量嵌入写入 Milvus。
- **配置前缀**：`RAG_`（如 `RAG_MILVUS_URI`、`RAG_MILVUS_COLLECTION_NAME`、`RAG_EMBEDDING_MODEL_PATH`、`RAG_SEARCH_TOP_K`）。
  环境变量统一写在根目录 `.env`（含密钥，已 git 忽略）。
- 不要把 chunks 数据或嵌入结果放到 `data/` 目录；中间产物放 `cache/`，知识库产出放 `knowledge-store/`。
- **唯一数据源**：全量切分产物只能由 `python -m agent.load_documents` 生成到 `cache/chunks.json`。
  **禁止**把 `cache/_all_chunks.json` 当摄入源——它是 `testself/test_all_documents.py` 的副产物，
  图片落在 pytest `tmp_path`，且历史上出现过 48 条 OCR 失败与 3 组主键重复
  （见 `plan/phase2_alignment_2026-10-19.md` P0-1）。
- **`testself/` 只写 `tmp_path`**，不得写 `cache/` 或 `knowledge-store/`。
- **OCR 失败结果不得写入缓存**：`cache/ocr_cache.json` 以图片 MD5 为键，
  一旦把 traceback 缓存进去，后续运行会永久命中失败结果而不重试。
- **BGE-M3 模型必须进程级复用**：不要在每次检索里 new 一个 `BGE3Embedding`，
  CPU 下模型冷加载 22~30s，会让单查询从 ~1s 退化到 ~8s。
- **RRF 分数不能当相关性阈值**：k=60 时 Top-5 分数挤在 `0.0300~0.0328`（理论上限 `2/61`），
  没有区分度。可信度门控要用 dense cosine（有效查询实测 `0.53~0.75`，无关查询 `0.42~0.51`），
  当前 `RAG_SEARCH_SCORE_THRESHOLD=0.52`。
- **`chunk_id` 是 Milvus 主键**：当前为位置型
  `{doc_slug}_{txt|img|tbl}_pXXX_iXXXX`，`content_hash` 仅为普通字段。
  改动前必读 `plan/think or choice/chunk_id_design.md`，不要再退回内容哈希主键。

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
docker compose ps                 # 查看健康状态（三个容器都应为 healthy）

# 文档切分（全量导出唯一入口，默认输出 cache/chunks.json）
python -m agent.load_documents
python -m agent.load_documents --docs-dir docs --output cache/chunks.json

# 查看 Milvus 库内数据
python tools/view_milvus_data.py --overview
python tools/view_milvus_data.py --type image --module 采购管理 --limit 3
python tools/view_milvus_data.py --search "采购入库的操作流程"

# 框架自测（不在 pytest.ini 的 testpaths 内，必须显式给路径）
pytest testself/test_pw_core_base_page.py -v
pytest testself/test_pw_core_driver.py -v
pytest testself/test_md_processor.py -v
pytest testself/test_pdf_processor.py -v -s          # 含 OCR，首次很慢
pytest testself/test_embedding_retrieval.py -v -s    # 需 Milvus 已启动，且会加载 BGE-M3

# RAG 数据摄入 / 校验（RAGPipeline CLI 尚未实现，当前用底层 API）
python -c "from agent.embedding import ingest; ingest('cache/chunks.json', validate_only=True)"
python -c "from agent.embedding import ingest; print(ingest('cache/chunks.json'))"
# 需要物理行数也回到 235 时：
python -c "from agent.schema import drop_collection; from agent.embedding import ingest; drop_collection(); ingest('cache/chunks.json')"
# 检索：
python -c "from agent.embedding import hybrid_search; [print(r.summary()) for r in hybrid_search('采购入库的操作流程', filters={'module':'采购管理'})]"

# 环境准备
# 编辑根目录 .env，填写 RAG_LLM_API_KEY / RAG_MILVUS_URI 等
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
4. **向量库**：**Milvus 单机版（Docker Compose：etcd + MinIO + milvus-standalone，19530/9091）**，dense + sparse 双路召回、RRFRanker 融合；按 `doc_version` 分区 + collection alias 切换（**此项尚未实现**），方案见 `plan/think or choice/rag_retrieval_design.md`。
5. **AIGC 采用混合架构**（非纯 Agentic）：RAG 检索 + 测试规划 → 自动收集 Page 类签名 → LLM 按模板生成 → AST 校验后写入 `testcases/`。理由：兼顾精度、延迟与 token 成本。
6. **AIGC 中间层用 YAML Spec，不用 Excel**：Spec 是真源（可 Pydantic 校验、可 git diff），
   Excel 只作为给业务评审的导出视图。LLM 不直接写 `testcases/*.py`，代码由 Jinja2 模板渲染。
   见 `plan/think or choice/aigc_code_generation_strategy.md`。
7. **Playwright MCP 限定为「生成期探测 + 验证期 dry-run」**，不取代 `pw_core/`，
   测试运行期仍走自有封装（保留 trace/截图/artifacts 能力）。同上文档 §4。
8. **父子分层检索不做**：235 chunks、最大分组仅 6 个、层级仅 3 层，
   用 metadata 过滤 + `chunk_index` 邻居召回即可等效。见 `plan/think or choice/parent_child_analysis.md`。

## 8. 修改时的注意事项

- 不要删除 `agent/`、`docs/`、`knowledge-store/`、`plan/`、`.codex/`：它们是未完成模块和设计依据。
- 不要往 `data/` 放非 YAML 文件；不要恢复 `data_loader.py` 到 `data/` 里。
- 不要绕过 `parametrize` 传参；不要在测试里重复登录/硬编码 URL 或凭证。
- 改动 fixture 或 `PlaywrightConfig` 默认值时，同步更新 README 和本文件。
- 新增依赖请写进 `pyproject.toml`，并保持 `uv.lock` 同步。
- 改 RAG 层（`agent/`、`tools/document/`）时，同步更新 `README.md`、本文件 §2/§4.5，
  以及 `plan/phase2_alignment_2026-10-19.md` 的缺陷清单状态。
- 不要新增第二份 docker-compose；根目录那份是唯一权威。
- 不要让 `testself/` 的用例写生产数据目录（`cache/`、`knowledge-store/`）。
- `ingest()` 已是幂等的「先按 `source_doc` 删除 → 再 upsert」，并带脏数据守卫；
  但 Milvus `delete` 是 tombstone，重复 ingest 后 `num_entities` 会暂时偏大，
  验收看 `count(*)`。需要物理行数也回到 235 时，用 drop + 单次重建。

## 9. 参考文档

- `plan/initial_project_plan_2026-10-17.md`：完整架构、RAG/LLM/自愈设计与路线图
- `plan/phase2_alignment_2026-10-19.md`：**当前进度对齐、缺陷清单（P0/P1/P2）、下一步计划与验收标准**
- `plan/phase2_5_completion_2026-10-19.md`：Phase 2.5 修复清单、验收数据与已知限制
- `plan/think or choice/phase1_implementation_summary.md`：Phase 1 实施清单
- `plan/think or choice/rag_retrieval_design.md`：Phase 2 检索层选型分析（三种方案对比、Milvus schema、嵌入策略、实施计划）
- `plan/think or choice/chunking_measures.md`：文档切分方案（PDF/MD 切分粒度、图片处理、caption 策略）
- `plan/think or choice/chunk_id_design.md`：chunk_id 方案分析（中文可读性 vs 内容哈希 vs 位置型主键）
- `plan/think or choice/aigc_code_generation_strategy.md`：Phase 3 三条路线选型（Excel 中间层 / LLM 直调工具 / MCP 托管）
- `plan/think or choice/parent_child_analysis.md`：父子分层检索论证（结论：当前不需要）
- `plan/think or choice/pdf_processing_optimization.md`：PDF/OCR 性能优化记录
- `问题优化清单.md`：PDF 处理性能优化的 STAR 复盘（建议后续并入 `plan/`）
- `.codex/skills/pom-ui-automation-generator/SKILL.md`：POM 代码生成规范
- `.codex/skills/rag-knowledge-base/SKILL.md`：RAG 知识库规范（`references/` 多数文件仍为空，待填充）
- `README.md`：面向使用者的快速开始
