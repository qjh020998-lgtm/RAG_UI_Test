"""ERP 测试数据准备用的 HTTP API 客户端。

UI 做数据准备慢且容易不稳定（新建采购单、审核单据等）。当 ERP 提供
API 时，测试可以用本客户端准备数据，把浏览器留给真正的交互和断言。

本客户端保持通用：接口路径和 payload 放在 Page/Test 层，不写进本包。
"""

from __future__ import annotations

from typing import Any

import httpx

from pw_core.conf import ApiConfig


class ApiError(RuntimeError):
    """ERP API 返回非 2xx 时抛出。"""

    def __init__(self, method: str, url: str, response: httpx.Response):
        self.method = method
        self.url = url
        self.status_code = response.status_code
        self.body = response.text
        super().__init__(
            f"{method} {url} -> HTTP {response.status_code}: {response.text[:500]}"
        )


class ApiClient:
    """支持 Token/Cookie 的同步 HTTP 客户端。"""

    def __init__(
        self,
        config: ApiConfig | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.config = config or ApiConfig()
        self.base_url = (base_url if base_url is not None else self.config.base_url).rstrip("/")
        self._token = token if token is not None else self.config.token
        self._headers = dict(headers or {})
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

        client_kwargs: dict[str, Any] = {
            "timeout": self.config.timeout,
            "verify": self.config.verify_ssl,
            "headers": self._headers,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = httpx.Client(**client_kwargs)

    @property
    def enabled(self) -> bool:
        """配置了 API base URL 时返回 True。"""
        return bool(self.base_url)

    @property
    def token(self) -> str:
        """当前 Bearer Token（未登录时为空）。"""
        return self._token

    def set_token(self, token: str) -> None:
        """设置/替换 Bearer Token。"""
        self._token = token
        self._headers["Authorization"] = f"Bearer {token}"
        self._client.headers["Authorization"] = f"Bearer {token}"

    def set_header(self, name: str, value: str) -> None:
        """设置/替换默认请求头（例如租户 ID）。"""
        self._headers[name] = value
        self._client.headers[name] = value

    def request(
        self,
        method: str,
        path: str,
        *,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """发送请求；失败时按需抛出 ``ApiError``。"""
        url = f"{self.base_url}{path}" if self.base_url else path
        response = self._client.request(method, path, **kwargs)
        if raise_for_status:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ApiError(method, url, response) from exc
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """发送 GET 请求。"""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """发送 POST 请求。"""
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """发送 PUT 请求。"""
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        """发送 PATCH 请求。"""
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """发送 DELETE 请求。"""
        return self.request("DELETE", path, **kwargs)

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        """发送请求并解析 JSON 响应。"""
        return self.request(method, path, **kwargs).json()

    def close(self) -> None:
        """关闭 HTTP 连接池。"""
        self._client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
