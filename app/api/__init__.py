"""HTTP 层：与既有 React 前端约定的 ``/api/*`` REST + SSE 接口。"""

from .routes import create_api_router

__all__ = ["create_api_router"]
