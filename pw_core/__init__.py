"""Playwright 核心包。

公开 API：
- ``PlaywrightConfig`` / ``ApiConfig``：配置（``PW_`` / ``PW_API_``）
- ``BasePage``：Page Object 基类（定位、动作、等待、断言、弹窗、表格）
- ``BaseTest`` / ``BasePageContract``：BasePage 契约检查
- ``WebDriver`` / ``DriverFactory`` / ``DriverManager``：Web/API 驱动层
"""

from pw_core.base_page import BasePage
from pw_core.base_test import BasePageContract, BaseTest
from pw_core.conf import ApiConfig, BrowserType, PlaywrightConfig
from pw_core.driver import (
    ApiClient,
    ApiError,
    DriverFactory,
    DriverManager,
    WebDriver,
)

__all__ = [
    "ApiClient",
    "ApiConfig",
    "ApiError",
    "BasePage",
    "BasePageContract",
    "BaseTest",
    "BrowserType",
    "DriverFactory",
    "DriverManager",
    "PlaywrightConfig",
    "WebDriver",
]
