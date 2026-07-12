"""基础认证与租户隔离。

TODO: 当前未接入路由依赖注入。后续需将 extract_context 作为 FastAPI
Depends() 集成到 API 路由中，实现真正的认证与租户隔离。
"""

from typing import Optional

from fastapi import Header, HTTPException


class RequestContext:
    """承载请求的身份信息和租户隔离上下文。"""

    def __init__(
        self,
        tenant_id: str,
        user_id: str,
        request_id: str,
        thread_id: Optional[str] = None,
    ):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.request_id = request_id
        self.thread_id = thread_id or ""


def extract_context(
    request_id: str,
    x_tenant_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    x_thread_id: Optional[str] = Header(None),
) -> RequestContext:
    """从 HTTP Header 提取请求上下文。

    生产环境中应验证 JWT Token 并强制鉴权。
    MVP 阶段接受基于 Header 的身份信息，并提供默认值。
    """
    tenant_id = x_tenant_id or "default"
    user_id = x_user_id or "anonymous"

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        thread_id=x_thread_id,
    )
