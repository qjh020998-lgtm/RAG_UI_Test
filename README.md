# RAG + Playwright + LLM UI 自动化测试框架

面向自研 ERP 系统的智能 UI 自动化测试框架，结合 POM 模式、RAG 知识库和 LLM 智能辅助。

## 特性

- **POM 模式**：Page Object 分层，测试用例只调用页面方法
- **Accessibility Tree 定位**：语义化定位器，减少前端重构影响
- **数据驱动**：YAML 管理测试数据，`@pytest.mark.parametrize` 注入参数
- **RAG 知识库**（主链路已跑通）：业务文档切分 + OCR → BGE-M3 dense/sparse 双向量 → Milvus 混合检索
- **LLM 辅助**（规划中）：Qwen 双模型策略，AIGC 用例生成与定位自愈

## 技术栈

- 测试框架：Pytest 9.1.1+
- 浏览器自动化：Playwright 1.64.0+
- 配置管理：Pydantic-Settings
- 数据管理：PyYAML + Pydantic
- 文档处理：PyMuPDF（PDF 解析/图片提取）+ RapidOCR（截图文字识别）
- 向量嵌入：BGE-M3（FlagEmbedding，本地推理，dense 1024 维 + sparse 词权重）
- 向量库：Milvus standalone v2.5.14（Docker Compose：etcd + MinIO + milvus）+ pymilvus
- 规划中：LangChain / LangGraph（AIGC 编排）

## 项目结构

```
RAG_UI_Test/
├── agent/                  # AI Agent 层
│   ├── config.py           # RAG 配置（RAG_ 前缀）
│   ├── schema.py           # Milvus collection schema + 索引管理
│   ├── embedding.py        # BGE-M3 嵌入 + 数据摄入 + 混合检索
│   ├── load_documents.py   # 文档切分统一入口
│   └── ...                 # app/models/rag_pipline/cache/security/monitering 仍为占位
├── tools/document/         # 文档处理层（MD / PDF 解析切分 + OCR）
├── tools/playwright/       # Playwright 封装
│   ├── conf.py             # 浏览器配置
│   ├── locators.py         # A11y 定位器
│   ├── actions.py          # 浏览器操作
│   └── browser.py          # 浏览器生命周期
├── tools/view_milvus_data.py  # Milvus 数据浏览与检索演示
├── page/                   # Page Object 层
│   ├── base_page.py        # 页面基类
│   └── login_page.py       # 登录页
├── data/                   # 只放 YAML 测试数据
│   ├── config.yaml         # base_url 等配置
│   └── login_cases.yaml    # 登录测试数据
├── data_loader.py          # YAML 加载 + Pydantic 校验
├── testcases/              # 测试用例
│   ├── conftest.py         # Pytest fixtures
│   └── test_login.py       # 登录测试（parametrize）
├── testself/               # 框架自测（文档处理、嵌入检索质量）
├── docs/                   # 业务文档（RAG 知识库来源）
├── cache/                  # 切分产物与 OCR 缓存（git 忽略）
├── knowledge-store/        # 知识库产物（导出/评测/备份）
├── plan/                   # 项目计划与阶段总结
├── docker-compose.yml      # Milvus 部署
├── volumes/                # Milvus/etcd/MinIO 数据（git 忽略）
├── .env.example            # RAG_ 环境变量样例
├── artifacts/              # 运行时截图/trace（自动生成，已忽略）
├── AGENTS.md               # 给 AI 助手的项目说明
└── main.py                 # 入口示例
```

## 快速开始

### 1. 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e .

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 运行测试

```bash
# 运行所有测试
pytest

# 运行冒烟测试
pytest -m smoke

# 运行特定测试文件
pytest testcases/test_login.py

# 只查看参数化展开结果（不启动浏览器）
pytest --collect-only -q

# 详细输出
pytest -v -s
```

### 3. 调试

```bash
# 显示浏览器窗口并放慢操作
PW_HEADLESS=false PW_SLOW_MO=500 pytest testcases/test_login.py -v -s
```

## RAG 知识库

### 1. 准备环境

```bash
# 复制环境变量样例并填写（.env 已被 git 忽略）
copy .env.example .env

# 启动 Milvus（etcd + MinIO + milvus-standalone，首次会拉镜像）
docker compose up -d
docker compose ps          # 三个容器都应为 healthy
```

BGE-M3 模型需已下载到本地，路径由 `RAG_EMBEDDING_MODEL_PATH` 指定，默认：
`D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master`。

### 2. 文档切分

把业务文档放进 `docs/`（支持 `.md` 与 `.pdf`），然后：

```bash
python -m agent.load_documents
```

产物：

- `cache/chunks.json`：全量切分结果（摄入 Milvus 的唯一数据源）
- `knowledge-store/images/`：从 PDF 提取的截图
- `cache/ocr_cache.json`：OCR 结果缓存（按图片 MD5，二次运行秒级完成）

当前 `docs/` 下 4 份文档切出 **235 个 chunk**：text 103 / image 102 / table 30。

### 3. 写入向量库

```python
from agent.embedding import ingest

ingest("cache/chunks.json")     # BGE-M3 批量嵌入 → 写入 erp_knowledge
```

> ✅ Phase 2.5 起 `ingest()` 会先校验 chunks（重复主键、OCR 失败/traceback、
> 绝对图片路径都会直接拒绝写入），再按 `source_doc` 删除旧行并 upsert。
> 每次摄入会在 `knowledge-store/manifests/{timestamp}.json` 留下来源、MD5、
> 类型分布、模型路径与耗时。
>
> 不要用 `cache/_all_chunks.json`（历史测试副产物，可能含 OCR 失败文本）。

### 4. 检索

```python
from agent.embedding import hybrid_search

results = hybrid_search("采购入库的操作流程", filters={"module": "采购管理"})
for r in results:
    print(r.summary())
```

- dense + sparse 双路召回，RRFRanker(k=60) 融合
- 支持过滤：`module` / `chunk_type` / `source_doc` / `section_title` / `is_plugin`
- **dense cosine 门控**：`RAG_SEARCH_SCORE_THRESHOLD` 默认 `0.52`
  （有效查询实测 0.53~0.75，无关查询 0.42~0.51）；无关查询返回空列表
- 进程级 BGE-M3 单例：稳态单查询 **245~294ms**（CPU），Milvus 引擎侧 6~13ms
- 结果按 `(source_doc, page, section_title, chunk_type)` 去重，避免同图副本挤占 Top-K

命令行浏览与检索演示：

```bash
python tools/view_milvus_data.py --overview
python tools/view_milvus_data.py --type image --module 采购管理 --limit 3
python tools/view_milvus_data.py --search "零售出库页面有哪些按钮"
```

### 5. 框架自测

`testself/` 不在 `pytest.ini` 的 `testpaths` 内，需显式指定路径：

```bash
pytest testself/test_md_processor.py -v
pytest testself/test_pdf_processor.py -v -s          # 含 OCR，首次较慢
pytest testself/test_embedding_retrieval.py -v -s    # 需 Milvus 已启动
```

## 数据驱动测试

测试数据统一放在 `data/*.yaml`，由 `data_loader.py` 加载并校验，再用
`@pytest.mark.parametrize` 注入用例：

```yaml
# data/login_cases.yaml
- name: valid_default
  username: "jsh"
  password: "123456"
  expect_success: true

- name: wrong_password
  username: "jsh"
  password: "wrong_password"
  expect_success: false
```

```python
# testcases/test_login.py
LOGIN_CASES = load_login_cases()


class TestLogin:
    @pytest.mark.parametrize("case", LOGIN_CASES, ids=[c.name for c in LOGIN_CASES])
    def test_login(self, login_page: LoginPage, case) -> None:
        login_page.goto_login()
        login_page.login_and_wait(
            case.username, case.password, expect_success=case.expect_success
        )
```

约定：`data/` 目录只允许 `.yaml` 文件，加载逻辑放在根目录 `data_loader.py`。

## 配置

- `data/config.yaml`：`base_url`
- 环境变量（前缀 `PW_`）：`PW_BROWSER_TYPE`、`PW_HEADLESS`、`PW_SLOW_MO`、
  `PW_TRACE_ENABLED`、`PW_VIEWPORT_WIDTH` 等，详见 `tools/playwright/conf.py`
- 环境变量（前缀 `RAG_`）：写在 `.env`（从 `.env.example` 复制），分四组，详见 `agent/config.py`
  - `RAG_MILVUS_*`：`URI`、`COLLECTION_NAME`、`COLLECTION_ALIAS`、`TIMEOUT`
  - `RAG_EMBEDDING_*`：`MODEL_PATH`、`DEVICE`、`USE_FP16`、`BATCH_SIZE`、`DENSE_DIM`、`MAX_LENGTH`
  - `RAG_LLM_*`：`API_KEY`、`BASE_URL`、`FAST_MODEL`、`STRONG_MODEL`、`TEMPERATURE`、`MAX_TOKENS`
  - `RAG_SEARCH_*`：`TOP_K`、`DENSE_TOP_K`、`SPARSE_TOP_K`、`RRF_K`、`SCORE_THRESHOLD`

```bash
export PW_BROWSER_TYPE=firefox
export PW_HEADLESS=false
export PW_TRACE_ENABLED=true
```

## 测试标记

- `@pytest.mark.smoke`：冒烟测试（关键路径）
- `@pytest.mark.regression`：回归测试
- `@pytest.mark.e2e`：端到端流程

## 开发指南

### 新增页面对象

1. 在 `page/` 创建 `xxx_page.py`，继承 `BasePage`
2. 用 `@property` 定义元素定位器（优先 A11y 语义定位）
3. 实现业务方法与 `assert_*` 断言
4. 在 `page/__init__.py` 导出

### 新增测试数据

1. 在 `data/` 新建或编辑 `.yaml`
2. 在 `data_loader.py` 添加 Pydantic 模型与加载函数
3. 在测试模块加载数据并 `@pytest.mark.parametrize` 传参

### 新增测试用例

1. 在 `testcases/` 创建 `test_xxx.py`
2. 使用 fixtures 获取 `login_page` 等 Page Object
3. 参数全部通过 `@pytest.mark.parametrize` 传入，遵循 Arrange-Act-Assert

## 项目计划

- 完整架构与路线图：`plan/initial_project_plan_2026-10-17.md`
- **当前进度对齐与下一步计划**：`plan/phase2_alignment_2026-10-19.md`
- **Phase 2.5 完成报告**：`plan/phase2_5_completion_2026-10-19.md`
- Phase 1 实施总结：`plan/think or choice/phase1_implementation_summary.md`
- 选型与决策文档：`plan/think or choice/`
  - `rag_retrieval_design.md`：检索层方案对比与 Milvus schema
  - `chunking_measures.md`：文档切分与图片处理方案
  - `chunk_id_design.md`：chunk_id 主键方案分析
  - `aigc_code_generation_strategy.md`：Phase 3 三条生成路线选型
  - `parent_child_analysis.md`：父子分层检索论证（结论：不需要）
  - `pdf_processing_optimization.md`：PDF/OCR 性能优化记录
- AI 协作说明：`AGENTS.md`
- POM 代码生成规范：`.codex/skills/pom-ui-automation-generator/SKILL.md`

## 当前进度

| 阶段 | 状态 |
|------|------|
| Phase 1 基础设施层（Playwright / POM / 数据驱动） | ✅ 已完成 |
| Phase 2 RAG 知识库（切分 + OCR + BGE-M3 + Milvus 混合检索） | ✅ 主链路已跑通，库内 235 条 |
| Phase 2.5 数据质量与工程加固 | ✅ 已完成（位置型主键、幂等 ingest、OCR 修复、dense 门控、manifest） |
| Phase 3 AIGC 测试用例生成 | ⏳ 未开始，方案已定 |
| Phase 4 控件定位自愈 | ⏳ 未开始 |
| Phase 5 监控与报告 | ⏳ 未开始 |

## 下一步

- [x] Phase 2.5：修数据源污染、稳定 chunk_id、检索性能与可信度门控、摄入溯源
- [ ] Phase 3：Spec 中间层 + 模板化代码生成 + AST 校验 + 试跑闭环
- [ ] Phase 4：自愈机制
- [ ] Phase 5：监控与报告

## 许可证

内部项目，保留所有权利。
