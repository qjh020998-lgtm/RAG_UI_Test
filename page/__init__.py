"""Page Object package."""

from pw_core.base_page import BasePage
from page.login_page import LoginPage
from page.purchase_page import PurchasePage
from page.retail_page import RetailPage

__all__ = [
    "BasePage",
    "LoginPage",
    "PurchasePage",
    "RetailPage",
]
