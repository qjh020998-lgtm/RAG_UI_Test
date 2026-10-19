"""RAG + Playwright + LLM UI 自动化测试框架。

这是一个面向自研 ERP 系统的智能 UI 自动化测试框架，
结合了 POM 模式、RAG 知识库和 LLM 智能辅助功能。
"""


def main():
    """框架入口示例。"""
    print("=" * 60)
    print("RAG + Playwright + LLM UI 自动化测试框架")
    print("=" * 60)
    print()
    print("框架组件:")
    print("  - pw_core/: Playwright 核心封装（Web/API Driver + BasePage）")
    print("  - page/: Page Object 页面类")
    print("  - data/: YAML 测试数据（只放 .yaml 文件）")
    print("  - data_loader.py: YAML 加载与 Pydantic 校验")
    print("  - testcases/: Pytest 测试用例（parametrize 数据驱动）")
    print("  - agent/: AI Agent 层（RAG + LLM，待实现）")
    print()
    print("使用方法:")
    print("  1. 运行所有测试: pytest")
    print("  2. 运行冒烟测试: pytest -m smoke")
    print("  3. 运行特定测试: pytest testcases/test_login.py")
    print("  4. 查看详细输出: pytest -v -s")
    print()
    print("文档:")
    print("  - AGENTS.md")
    print("  - plan/initial_project_plan_2026-10-17.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
