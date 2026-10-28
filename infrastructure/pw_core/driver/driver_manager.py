from __future__ import annotations
from utils.logger import get_logger
from typing import Any

log = get_logger(__name__)

class DriverManager:
    """单例模式：保证每个测试会话只有一个驱动实例。

       所有 Page Object 都可以通过
       ``DriverManager().get_driver()`` 获取同一个驱动对象。
       """

    _instance = None
    _driver = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            log.debug("DriverManager 实例已创建")
        return cls._instance

    def set_driver(self, driver: Any) -> None:
        """设置当前活跃driver。"""
        if driver is None:
            raise ValueError("driver 不能为 None")

        self._driver = driver
        log.debug("DriverManager：driver已设置 -> %s", type(driver).__name__)

    def get_driver(self) -> Any:
        """获取当前活跃driver；未初始化时直接报错。"""
        if self._driver is None:
            raise RuntimeError(
                "驱动尚未初始化。请确认 conftest.py 在测试开始前调用了 "
                "DriverManager().set_driver()。"
            )
        return self._driver

    def quit_driver(self) -> None:
        """关闭并清空当前驱动。

        兼容两种驱动：
        - Selenium：``quit()``
        - Playwright WebDriver：``close()``
        """
        if self._driver:
            self._driver.quit()
            self._driver = None

    @classmethod
    def reset(cls) -> None:
        """清除单例，供同一进程中的下一个测试会话使用。

        注意：reset 不会自动关闭驱动；应先调用 ``quit_driver()``。
        """
        cls._instance = None
        cls._driver = None
        log.debug("DriverManager：单例已重置")