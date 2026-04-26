"""Control-plane auth: shared-secret header for FastAPI routes."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .settings import get_settings


def require_control_plane_token(
    x_control_plane_token: str | None = Header(default=None, alias="X-Control-Plane-Token"),
) -> None:
    expected = get_settings().control_plane_token
    if not expected or expected == "change-me-in-production":
        # Fail closed if the operator forgot to set a real token.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="control_plane_token_not_configured",
        )
    if x_control_plane_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad_token")
