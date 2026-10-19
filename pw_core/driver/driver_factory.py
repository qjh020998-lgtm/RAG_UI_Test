"""驱动工厂：按平台/驱动类型创建 WebDriver。

参考 astra 的 Factory 模式：调用方只传 ``platform``，由工厂选择具体驱动。
当前只有 ``web``，后续可注册 ``api``、``mobile`` 等。
"""

from __future__ import annotations

from typing import Callable

from pw_core.conf import PlaywrightConfig
from pw_core.driver.web_driver import WebDriver

DriverBuilder = Callable[[PlaywrightConfig | None], object]


class DriverFactory:
    """驱动注册表与创建入口。"""

    _registry: dict[str, DriverBuilder] = {
        "web": lambda config=None: WebDriver(config),
    }

    @classmethod
    def register(cls, platform: str, builder: DriverBuilder) -> None:
        """注册一个驱动构造器。"""
        cls._registry[platform.lower()] = builder

    @classmethod
    def create(
        cls,
        platform: str = "web",
        config: PlaywrightConfig | None = None,
    ) -> WebDriver:
        """按平台创建驱动；未知平台直接报错。"""
        key = platform.lower()
        builder = cls._registry.get(key)
        if builder is None:
            supported = ", ".join(sorted(cls._registry))
            raise ValueError(f"未知驱动平台: {platform!r}；已支持: {supported}")
        return builder(config)  # type: ignore[return-value]

    @classmethod
    def create_web_driver(
        cls,
        config: PlaywrightConfig | None = None,
    ) -> WebDriver:
        """创建 WebDriver 的快捷方法。"""
        return cls.create("web", config)
