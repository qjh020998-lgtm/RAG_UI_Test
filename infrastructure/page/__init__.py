"""Page Object package."""

from infrastructure.pw_core.base_page import BasePage
from infrastructure.page.login_page import LoginPage
from infrastructure.page.purchase_page import PurchasePage
from infrastructure.page import RetailPage

__all__ = [
    "BasePage",
    "LoginPage",
    "PurchasePage",
    "RetailPage",
]
