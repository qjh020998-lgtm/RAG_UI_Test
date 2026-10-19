"""BasePage 的兼容导入。

实现已迁移到 ``pw_core.base_page``。新代码请直接从 ``pw_core`` 导入；
本文件只用于兼容旧的 ``page.base_page`` 引用。
"""

from pw_core.base_page import BasePage

__all__ = ["BasePage"]
