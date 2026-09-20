"""Health endpoints (stubs). Filled out in P-12.

P-00: router must exist, endpoints are placeholders.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness: process is alive."""
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready() -> dict[str, str]:
    """Readiness: infra reachable + outbox lag check (stub until P-12)."""
    return {"status": "ok"}
