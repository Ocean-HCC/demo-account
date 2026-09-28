"""接口依赖：取服务容器、校验 X-API-Key。"""

from __future__ import annotations

from fastapi import Request

from ..services.container import Container
from ..services.errors import ServiceError


def get_container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


def require_api_key(request: Request) -> None:
    c: Container = request.app.state.container
    key = c.settings.api_key
    if key and request.headers.get("X-API-Key") != key:
        raise ServiceError("UNAUTHORIZED", "缺少或错误的 X-API-Key", 401)
