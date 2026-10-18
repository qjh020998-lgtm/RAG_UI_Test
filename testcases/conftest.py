"""Pytest fixtures for UI automation tests."""

import pytest
from playwright.sync_api import Page, Browser, BrowserContext

from data_loader import FrameworkConfig, load_config
from tools.playwright.conf import PlaywrightConfig
from tools.playwright.browser import BrowserManager
from page.login_page import LoginPage


# ========== Fixtures ==========

@pytest.fixture(scope="session")
def framework_config() -> FrameworkConfig:
    """框架配置（来自 data/config.yaml，会话级别）。"""
    return load_config()


@pytest.fixture(scope="session")
def config(framework_config: FrameworkConfig) -> PlaywrightConfig:
    """Playwright 配置（会话级别，可用 PW_ 前缀环境变量覆盖）。"""
    return PlaywrightConfig(base_url=framework_config.base_url)


@pytest.fixture(scope="session")
def browser_manager(config: PlaywrightConfig) -> BrowserManager:
    """浏览器管理器（会话级别）。"""
    manager = BrowserManager(config)
    yield manager
    manager.close_all()


@pytest.fixture(scope="session")
def browser(browser_manager: BrowserManager) -> Browser:
    """浏览器实例（会话级别）。"""
    return browser_manager.start_browser()


@pytest.fixture
def context(browser_manager: BrowserManager) -> BrowserContext:
    """浏览器上下文（测试级别）。

    每个测试用例使用独立的上下文，确保测试隔离。
    """
    ctx = browser_manager.new_context()
    yield ctx
    browser_manager.close_context()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    """页面对象（测试级别）。"""
    pg = context.new_page()
    yield pg
    pg.close()


@pytest.fixture
def login_page(page: Page, config: PlaywrightConfig) -> LoginPage:
    """登录页 Page Object（测试级别）。"""
    return LoginPage(page, base_url=config.base_url)
