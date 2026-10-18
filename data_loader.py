"""YAML 测试数据加载与校验。

约定：``data/`` 目录只存放 ``*.yaml`` 数据文件，加载与校验逻辑统一放在本模块。
测试用例不直接读文件，而是通过本模块拿到经过 Pydantic 校验的数据对象。
"""

from pathlib import Path
from typing import Type, TypeVar

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

T = TypeVar("T", bound=BaseModel)


# ========== Pydantic Models ==========

class LoginCase(BaseModel):
    """一条登录测试数据（对应 data/login_cases.yaml 中的一项）。"""

    name: str
    username: str
    password: str
    expect_success: bool


class FrameworkConfig(BaseModel):
    """框架运行配置（对应 data/config.yaml）。"""

    base_url: str


# ========== 通用加载方法 ==========

def load_yaml(file_path: str | Path) -> dict | list:
    """加载 YAML 文件。

    Args:
        file_path: YAML 文件路径（相对 ``data/`` 目录或绝对路径）

    Returns:
        解析后的字典或列表
    """
    path = Path(file_path)

    if not path.is_absolute():
        path = DATA_DIR / path

    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_data_model(file_path: str | Path, key: str, model_class: Type[T]) -> T:
    """按 key 读取 YAML 中的一条记录并校验为 Pydantic 模型。"""
    data = load_yaml(file_path)

    if not isinstance(data, dict) or key not in data:
        available_keys = list(data) if isinstance(data, dict) else []
        raise KeyError(f"键 '{key}' 不存在于 {file_path}。可用键: {available_keys}")

    return model_class(**data[key])


# ========== 具体数据加载方法 ==========

def load_config() -> FrameworkConfig:
    """加载框架配置（base_url 等）。"""
    return FrameworkConfig(**load_yaml("config.yaml"))


def load_login_cases() -> list[LoginCase]:
    """加载全部登录测试数据，供 ``@pytest.mark.parametrize`` 使用。"""
    raw = load_yaml("login_cases.yaml")

    if not isinstance(raw, list):
        raise ValueError("login_cases.yaml 必须是以 '-' 开头的 YAML 列表")

    return [LoginCase(**item) for item in raw]
