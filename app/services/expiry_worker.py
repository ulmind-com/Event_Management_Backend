import asyncio
import logging
from datetime import datetime, timezone
from app.services.wallet_service import WalletService

logger = logging.getLogger("eventora.expiry_worker")

class ExpiryWorker:
    _task: asyncio.Task = None
    _running: bool = False

    @classmethod
    async def run_cycle(cls) -> dict:
        """Runs a single coin expiry cycle."""
        try:
            logger.info("Starting automated coin expiry cycle...")
            result = await WalletService.process_expired_coins()
            try:
                from app.services.vip_service import VIPService
                vip_exp_res = await VIPService.expire_pending_reservations()
                result["vip_expired_reservations"] = vip_exp_res.get("expired_count", 0)
            except Exception as e:
                logger.error(f"Error in VIP reservation expiry cycle: {e}")
            try:
                from app.services.merchandise_service import MerchandiseService
                merch_exp_res = await MerchandiseService.expire_pending_orders()
                result["merchandise_expired_orders"] = merch_exp_res.get("expired_orders_count", 0)
            except Exception as e:
                logger.error(f"Error in Merchandise order expiry cycle: {e}")

            logger.info(
                f"Automated coin expiry cycle complete. Lots expired: {result.get('lots_expired_count', 0)}, "
                f"Coins expired: {result.get('total_coins_expired', 0)}"
            )
            return result
        except Exception as e:
            logger.error(f"Error executing coin expiry cycle: {e}", exc_info=True)
            return {"error": str(e)}

    @classmethod
    async def _worker_loop(cls, interval_seconds: int = 3600):
        while cls._running:
            try:
                await cls.run_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in expiry worker loop: {e}", exc_info=True)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    @classmethod
    def start(cls, interval_seconds: int = 3600):
        if cls._running:
            return
        cls._running = True
        cls._task = asyncio.create_task(cls._worker_loop(interval_seconds))
        logger.info(f"Coin Expiry Worker started with interval {interval_seconds}s")

    @classmethod
    async def stop(cls):
        cls._running = False
        if cls._task and not cls._task.done():
            cls._task.cancel()
            try:
                await cls._task
            except asyncio.CancelledError:
                pass
        logger.info("Coin Expiry Worker stopped")
