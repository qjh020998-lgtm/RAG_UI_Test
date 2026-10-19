"""Playwright 核心配置。

所有配置都可以通过 ``PW_`` 前缀的环境变量覆盖，
例如 ``PW_HEADLESS=false``、``PW_TRACE_ENABLED=true``。
"""

from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class BrowserType(str, Enum):
    """支持的浏览器引擎。"""

    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class PlaywrightConfig(BaseSettings):
    """Playwright 浏览器与产物配置。"""

    model_config = SettingsConfigDict(env_prefix="PW_")

    browser_type: BrowserType = BrowserType.CHROMIUM
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    default_timeout: int = 30_000
    navigation_timeout: int = 60_000

    # 运行时产物
    artifacts_dir: str = "artifacts"
    screenshot_dir: str = "artifacts/screenshots"
    trace_dir: str = "artifacts/traces"
    download_dir: str = "artifacts/downloads"
    screenshot_on_failure: bool = True

    # 登录态复用
    auth_state_path: str = "artifacts/auth/storage_state.json"

    # 浏览器上下文
    ignore_https_errors: bool = False
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    base_url: str = ""

    # 追踪录制
    trace_enabled: bool = False
    trace_screenshots: bool = True
    trace_snapshots: bool = True
    trace_sources: bool = True


class ApiConfig(BaseSettings):
    """可选的 ERP API 配置（``PW_API_`` 前缀）。

    API 是可选能力：``base_url`` 为空时，``DriverManager`` 自动退回 Web。
    """

    model_config = SettingsConfigDict(env_prefix="PW_API_")

    base_url: str = ""
    token: str = ""
    timeout: float = 30.0
    verify_ssl: bool = True
