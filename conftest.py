"""UI 自动化测试的 pytest fixture。

Phase 3.0 基础设施：
- 会话级 WebDriver（Playwright 浏览器只启动一次）；
- 函数级隔离 context/page；
- 失败 trace + 截图；
- 可选 storage-state 登录态复用（``logged_in_page``）；
- 可选 API/Web 驱动选择（``driver_manager``）。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from data_loader import FrameworkConfig, load_config, load_login_cases
from page.login_page import LoginPage
from pw_core import (
    ApiClient,
    ApiConfig,
    DriverManager,
    PlaywrightConfig,
    WebDriver,
)


# ── pytest 钩子 ────────────────────────────────────────────────────

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """暴露 setup/call 报告，供 fixture 判断是否失败。"""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def _test_failed(request) -> bool:
    """当前用例的 setup 或 call 阶段失败时返回 True。"""
    for phase in ("setup", "call"):
        report = getattr(request.node, f"rep_{phase}", None)
        if report is not None and report.failed:
            return True
    return False


def _safe_name(name: str) -> str:
    """把用例名转换为可安全用于文件名的字符串。"""
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", name)[:120]


def _screenshot(page: Page, config: PlaywrightConfig, name: str) -> None:
    """失败截图；任何异常都不覆盖原始失败。"""
    if not config.screenshot_on_failure:
        return
    try:
        screenshot_dir = Path(config.screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        page.screenshot(
            path=str(screenshot_dir / f"{_safe_name(name)}-{timestamp}.png"),
            full_page=True,
        )
    except Exception:
        pass


# ── 浏览器 / context fixtures ──────────────────────────────────────

@pytest.fixture(scope="session")
def framework_config() -> FrameworkConfig:
    """来自 ``data/config.yaml`` 的框架配置。"""
    return load_config()


@pytest.fixture(scope="session")
def config(framework_config: FrameworkConfig) -> PlaywrightConfig:
    """Playwright 配置（``PW_`` 环境变量可覆盖默认值）。"""
    return PlaywrightConfig(base_url=framework_config.base_url)


@pytest.fixture(scope="session")
def web_driver(config: PlaywrightConfig) -> WebDriver:
    """会话级 WebDriver。"""
    driver = WebDriver(config)
    driver.start()
    yield driver
    driver.close()


@pytest.fixture(scope="session")
def browser(web_driver: WebDriver) -> Browser:
    """会话级浏览器进程（兼容旧 fixture 名称）。"""
    return web_driver.browser


@pytest.fixture(scope="session")
def api_config() -> ApiConfig:
    """可选 API 配置（``PW_API_BASE_URL`` 等）。"""
    return ApiConfig()


@pytest.fixture(scope="session")
def api_client(api_config: ApiConfig) -> ApiClient:
    """可选 ERP API 客户端；base_url 为空时自动禁用。"""
    client = ApiClient(api_config)
    yield client
    client.close()


@pytest.fixture(scope="session")
def driver_manager(
    web_driver: WebDriver,
    api_client: ApiClient,
) -> DriverManager:
    """按操作选择 API（数据准备）或 Web（UI 交互）。"""
    return DriverManager(web_driver=web_driver, api_client=api_client)


@pytest.fixture
def context(
    web_driver: WebDriver,
    request,
) -> BrowserContext:
    """函数级隔离 context。

    trace 只在用例失败时保存到 ``artifacts/traces``。
    """
    ctx = web_driver.new_context()
    yield ctx
    trace_name = request.node.name if _test_failed(request) else None
    web_driver.close_context(ctx, trace_name=trace_name)


@pytest.fixture
def page(
    context: BrowserContext,
    config: PlaywrightConfig,
    request,
) -> Page:
    """函数级页面，失败时自动截图。"""
    pg = context.new_page()
    yield pg
    if _test_failed(request):
        _screenshot(pg, config, request.node.name)
    pg.close()


# ── Page fixtures ──────────────────────────────────────────────────

@pytest.fixture
def login_page(page: Page, config: PlaywrightConfig) -> LoginPage:
    """登录页 Page Object（函数级）。"""
    return LoginPage(page, base_url=config.base_url)


# ── 登录态 fixtures ────────────────────────────────────────────────

@pytest.fixture(scope="session")
def auth_state(config: PlaywrightConfig) -> str:
    """可复用的 Playwright storage-state 文件路径。"""
    return config.auth_state_path


@pytest.fixture
def logged_in_page(
    web_driver: WebDriver,
    config: PlaywrightConfig,
    auth_state: str,
    request,
) -> Page:
    """带 ERP 登录态的页面。

    第一次使用时，用 ``data/login_cases.yaml`` 中第一个
    ``expect_success`` 用例登录并保存 storage state；后续测试直接复用。
    """
    state_path = Path(auth_state)
    ctx = web_driver.new_context(
        storage_state=state_path if state_path.exists() else None
    )
    pg = ctx.new_page()
    try:
        if not state_path.exists():
            cases = load_login_cases()
            login_case = next((case for case in cases if case.expect_success), None)
            if login_case is None:
                pytest.skip("login_cases.yaml 中没有 expect_success=true 的用例")

            login_page = LoginPage(pg, base_url=config.base_url)
            login_page.goto_login()
            login_page.login_and_wait(
                login_case.username,
                login_case.password,
                expect_success=True,
            )
            web_driver.save_storage_state(ctx, state_path)

        yield pg
    finally:
        if _test_failed(request):
            _screenshot(pg, config, request.node.name)
        web_driver.close_context(
            ctx,
            trace_name=request.node.name if _test_failed(request) else None,
        )
