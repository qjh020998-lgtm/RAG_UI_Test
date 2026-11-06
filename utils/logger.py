import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "reports" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 每次进程启动生成一个独立日志文件：test_run_YYYYmmdd_HHMMSS_<pid>.log
_RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"test_run_{_RUN_STAMP}_{os.getpid()}.log"

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_CONSOLE = logging.StreamHandler(stream=sys.stdout)
_CONSOLE.setLevel(logging.INFO)
_CONSOLE.setFormatter(_FORMATTER)

_FILE = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
_FILE.setLevel(logging.DEBUG)
_FILE.setFormatter(_FORMATTER)


def get_logger(name: str) -> logging.Logger:
    """返回统一写入本次运行日志文件的 logger。

    所有模块共享同一个文件 handler，避免每个模块各开一个文件；
    propagate=False 避免父子 logger 重复输出。
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.addHandler(_CONSOLE)
    logger.addHandler(_FILE)
    logger.propagate = False

    return logger
