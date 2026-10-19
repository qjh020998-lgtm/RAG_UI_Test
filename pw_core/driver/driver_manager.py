"""驱动管理器：按操作选择 API 或 Web。

管理器不决定测试做什么，只决定这次操作用哪个驱动：

- ``strategy="api"``：只用 API；未配置时抛错。
- ``strategy="web"``：只用浏览器。
- ``strategy="auto"``：API 可用且优先时走 API，否则走 Web。

参考 astra 的 ``driver_manager``：持有一个活跃 WebDriver，并负责
context 的创建与关闭。API 是本项目为 ERP 数据准备增加的扩展。
"""

from __future__ import annotations

from typing import Callable, TypeVar

from pw_core.conf import ApiConfig, PlaywrightConfig
from pw_core.driver.api_client import ApiClient
from pw_core.driver.driver_factory import DriverFactory
from pw_core.driver.web_driver import WebDriver

T = TypeVar("T")


class DriverManager:
    """持有一个 WebDriver 和一个可选的 ApiClient。"""

    def __init__(
        self,
        web_driver: WebDriver | None = None,
        api_client: ApiClient | None = None,
        *,
        prefer_api: bool = True,
        config: PlaywrightConfig | None = None,
        api_config: ApiConfig | None = None,
    ):
        self.web = web_driver or DriverFactory.create_web_driver(config)
        if api_client is None:
            candidate = ApiClient(api_config)
            api_client = candidate if candidate.enabled else None
        self.api = api_client
        self.prefer_api = prefer_api
        self.config = config or getattr(self.web, "config", None) or PlaywrightConfig()

    # ── 驱动选择 ───────────────────────────────────────────────────

    @property
    def page(self):
        """当前 Playwright 页面。"""
        return self.web.page

    @property
    def has_api(self) -> bool:
        """是否配置了 API 客户端。"""
        return self.api is not None and self.api.enabled

    def backend_for(self, strategy: str = "auto") -> str:
        """把策略解析为 ``"api"`` 或 ``"web"``。"""
        if strategy == "web":
            return "web"
        if strategy == "api":
            if not self.has_api:
                raise ValueError("请求 API 策略，但未配置 API base_url")
            return "api"
        if strategy != "auto":
            raise ValueError(f"未知驱动策略: {strategy!r}")
        return "api" if self.prefer_api and self.has_api else "web"

    def run(
        self,
        api_call: Callable[[ApiClient], T] | None = None,
        web_call: Callable[[WebDriver], T] | None = None,
        *,
        strategy: str = "auto",
    ) -> T:
        """在选定的驱动上执行一个操作。"""
        if api_call is None and web_call is None:
            raise ValueError("run() 至少需要 api_call 或 web_call")

        if api_call is None:
            return web_call(self.web)  # type: ignore[misc]
        if web_call is None:
            return api_call(self.api)  # type: ignore[arg-type]

        backend = self.backend_for(strategy)
        if backend == "api":
            return api_call(self.api)  # type: ignore[arg-type]
        return web_call(self.web)

    # ── Web 生命周期 ───────────────────────────────────────────────

    def start_web(
        self,
        storage_state: str | None = None,
        record_trace: bool | None = None,
    ):
        """启动浏览器并创建 context/page。"""
        self.web.start()
        self.web.new_context(
            storage_state=storage_state,
            record_trace=record_trace,
        )
        return self.web.new_page()

    def new_context(
        self,
        storage_state: str | None = None,
        record_trace: bool | None = None,
    ):
        """创建新的隔离 context。"""
        return self.web.new_context(
            storage_state=storage_state,
            record_trace=record_trace,
        )

    def close_context(self, context, trace_name: str | None = None) -> None:
        """关闭指定 context。"""
        self.web.close_context(context, trace_name=trace_name)

    def save_web_session(self, path: str | None = None) -> str:
        """保存当前浏览器登录态。"""
        context = self.web.current_context
        if context is None:
            raise RuntimeError("没有活动的浏览器 context")
        return self.web.save_storage_state(context, path)

    def close(self) -> None:
        """关闭 Web 和 API 资源。"""
        try:
            self.web.close()
        finally:
            if self.api is not None:
                self.api.close()

    def __enter__(self) -> "DriverManager":
        self.start_web()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
