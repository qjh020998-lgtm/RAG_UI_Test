"""Playwright browser configuration."""

from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class BrowserType(str, Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class PlaywrightConfig(BaseSettings):
    """Playwright browser configuration.

    Values can be overridden via environment variables prefixed with
    `PW_` (e.g. `PW_BROWSER_TYPE=firefox`).
    """

    model_config = SettingsConfigDict(env_prefix="PW_")

    browser_type: BrowserType = BrowserType.CHROMIUM
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    default_timeout: int = 30_000
    navigation_timeout: int = 60_000
    screenshot_dir: str = "artifacts/screenshots"
    trace_enabled: bool = False
    trace_dir: str = "artifacts/traces"
    download_dir: str = "artifacts/downloads"
    ignore_https_errors: bool = False
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    base_url: str = ""
