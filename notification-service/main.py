import asyncio
import logging
import signal

import httpx
import redis.asyncio as aioredis
import uvicorn

from config import get_settings
from drain import drain_loop
from ingestion import create_app
from logging_config import configure_logging
from reaper import reaper_loop
from telegram import TelegramRateLimiter

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    http_client = httpx.AsyncClient()
    rate_limiter = TelegramRateLimiter(
        global_rate_per_second=settings.telegram_global_rate_per_second,
        chat_min_interval_seconds=settings.telegram_chat_min_interval_seconds,
    )

    app = create_app(redis_client=redis_client, http_client=http_client, settings=settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            lifespan="on",
        )
    )

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    drain_task = asyncio.create_task(
        drain_loop(redis_client, http_client, settings, rate_limiter, stop_event),
        name="drain",
    )
    reaper_task = asyncio.create_task(
        reaper_loop(redis_client, http_client, settings, rate_limiter, stop_event),
        name="reaper",
    )

    try:
        await asyncio.wait(
            {server_task, drain_task, reaper_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        logger.info("Shutdown requested", extra={"event": "shutdown_requested"})
        stop_event.set()
        server.should_exit = True

        await asyncio.gather(server_task, drain_task, reaper_task, return_exceptions=True)
        await http_client.aclose()
        await redis_client.aclose()
        logger.info("Shutdown complete", extra={"event": "shutdown_complete"})


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())


if __name__ == "__main__":
    asyncio.run(main())
