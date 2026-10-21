import os
from pathlib import Path
from dotenv import load_dotenv

# 加载根目录 .env 文件
load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    """应用配置类，集中管理所有环境变量"""

    # 浏览器 / 平台配置
    BASE_URL: str = os.getenv("BASE_URL", "https://www.ambitionbox.com")
    BROWSER: str = os.getenv("BROWSER", "chromium")
    HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"
    PLATFORM: str = os.getenv("PLATFORM", "web")
    SLOW_MO: int = int(os.getenv("SLOW_MO", "0"))
    TIMEOUT: int = int(os.getenv("TIMEOUT", "30000"))

    # 浏览器窗口/视口
    FULLSCREEN: bool = os.getenv("FULLSCREEN", "true").lower() == "true"
    VIEWPORT_WIDTH: int = int(os.getenv("VIEWPORT_WIDTH", "1920"))
    VIEWPORT_HEIGHT: int = int(os.getenv("VIEWPORT_HEIGHT", "1080"))

    # 产物配置
    SCREENSHOT_ON_FAILURE: bool = os.getenv("SCREENSHOT_ON_FAILURE", "true").lower() == "true"
    VIDEO_ON_FAILURE: bool = os.getenv("VIDEO_ON_FAILURE", "false").lower() == "true"

    @classmethod
    def summary(cls) -> str:
        """返回当前配置的摘要信息"""
        return (
            f"平台={cls.PLATFORM} | "
            f"浏览器={cls.BROWSER} | "
            f"无头模式={cls.HEADLESS} | "
            f"全屏={cls.FULLSCREEN} | "
            f"基础URL={cls.BASE_URL}"
        )
