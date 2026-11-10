import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Callable, Any
from pymongo.errors import PyMongoError, OperationFailure
import motor.motor_asyncio
from app.core.database import client

logger = logging.getLogger("eventora.transactions")


@asynccontextmanager
async def database_transaction() -> AsyncGenerator[motor.motor_asyncio.AsyncIOMotorClientSession, None]:
    """
    Context manager providing atomic multi-document MongoDB ACID transaction guarantees.
    
    Usage:
        async with database_transaction() as session:
            await booking_collection.update_one(..., session=session)
            await ticket_collection.update_one(..., session=session)
            await issued_ticket_collection.insert_many(..., session=session)
            # If any exception occurs or is raised, all changes are rolled back automatically!
    """
    session = await client.start_session()
    try:
        async with session.start_transaction():
            yield session
    except Exception as e:
        logger.warning(f"Database transaction aborted due to error: {e}")
        # When exiting the block with an exception, Motor automatically aborts the transaction
        raise
    finally:
        await session.end_session()


async def run_in_transaction_with_retry(
    transaction_body_fn: Callable[[motor.motor_asyncio.AsyncIOMotorClientSession], Any],
    max_retries: int = 3,
    backoff_delay: float = 0.15
) -> Any:
    """
    Executes a transactional operation with automated retry for TransientTransactionErrors.
    Complies with MongoDB official transaction guidelines for high-concurrency environments
    (handling write conflicts and transient network failovers smoothly).
    """
    session = await client.start_session()
    try:
        for attempt in range(max_retries):
            try:
                async with session.start_transaction():
                    result = await transaction_body_fn(session)
                    return result
            except PyMongoError as exc:
                if exc.has_error_label("TransientTransactionError") and attempt < max_retries - 1:
                    logger.warning(
                        f"TransientTransactionError encountered on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying after {backoff_delay * (2 ** attempt)}s..."
                    )
                    await asyncio.sleep(backoff_delay * (2 ** attempt))
                    continue
                raise
    finally:
        await session.end_session()

