"""Basic authentication and tenant isolation."""

from typing import Optional

from fastapi import Header, HTTPException


class RequestContext:
    """Carries identity and isolation context through the request."""

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
    """Extract request context from headers.

    In production, this would validate JWT tokens and enforce auth.
    For MVP, we accept header-based identity with defaults.
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
