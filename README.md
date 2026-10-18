# RAG + Playwright + LLM UI 自动化测试框架

面向自研 ERP 系统的智能 UI 自动化测试框架，结合 POM 模式、RAG 知识库和 LLM 智能辅助。

## 特性

- **POM 模式**：Page Object 分层，测试用例只调用页面方法
- **Accessibility Tree 定位**：语义化定位器，减少前端重构影响
- **数据驱动**：YAML 管理测试数据，`@pytest.mark.parametrize` 注入参数
- **RAG 知识库**（规划中）：基于业务文档生成测试用例
- **LLM 辅助**（规划中）：Qwen 3.8 双模型策略，定位自愈

## 技术栈

- 测试框架：Pytest 9.1.1+
- 浏览器自动化：Playwright 1.64.0+
- 配置管理：Pydantic-Settings
- 数据管理：PyYAML + Pydantic
- RAG：LangChain + LangGraph + Milvus（Docker，规划中）

## 项目结构

```
RAG_UI_Test/
├── agent/                  # AI Agent 层（RAG + LLM，占位待实现）
├── tools/playwright/       # Playwright 封装
│   ├── conf.py             # 浏览器配置
│   ├── locators.py         # A11y 定位器
│   ├── actions.py          # 浏览器操作
│   └── browser.py          # 浏览器生命周期
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
├── docs/                   # 业务文档（RAG 知识库来源）
├── knowledge-store/        # 知识库产物（导出/评测/备份）
├── plan/                   # 项目计划与阶段总结
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
- Phase 1 实施总结：`plan/phase1_implementation_summary.md`
- AI 协作说明：`AGENTS.md`
- POM 代码生成规范：`.codex/skills/pom-ui-automation-generator/SKILL.md`

## 下一步

- [ ] Phase 2：RAG 知识库构建
- [ ] Phase 3：AIGC 测试用例生成
- [ ] Phase 4：自愈机制
- [ ] Phase 5：监控与报告

## 许可证

内部项目，保留所有权利。
