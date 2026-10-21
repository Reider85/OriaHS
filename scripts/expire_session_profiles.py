import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.queries.user_context_profiles import expire_session_profiles
from app.db.session import get_async_session

logger = logging.getLogger(__name__)


async def run_expire_session_profiles() -> None:
    """Run session profile expiration cleanup."""
    try:
        logger.info("Starting session profile expiration")

        async for session in get_async_session():
            cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(hours=24)
            deleted_count = await expire_session_profiles(session, cutoff)

        logger.info(f"Expired {deleted_count} session profiles")

    except Exception as e:
        logger.error(f"Session profile expiration failed: {e}")
        raise


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    asyncio.run(run_expire_session_profiles())