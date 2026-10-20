"""Page Object package."""

from page.base_page import BasePage
from page.login_page import LoginPage
from page.purchase_page import PurchasePage

__all__ = [
    "BasePage",
    "LoginPage",
    "PurchasePage",
]
