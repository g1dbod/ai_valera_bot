import asyncio
import logging
import logging.handlers
import sys

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import TELEGRAM_BOT_TOKEN, LOG_FILE, LOG_LEVEL
from bot.database import init_db
from bot.handlers import api_handlers, model_handlers, chat_handlers, context_handlers, settings_handlers


def setup_logging() -> None:
    level = getattr(logging, LOG_LEVEL.upper(), logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler(sys.stdout)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    for h in (file_handler, stream_handler):
        h.setFormatter(logging.Formatter(fmt))
        h.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # silence noisy libs
    for noisy in ("httpcore", "httpx", "openai._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info("Logging initialised — level=%s file=%s", LOG_LEVEL, LOG_FILE)


async def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)

    log.info("Initialising database…")
    await init_db()

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Register routers
    for module in (
        chat_handlers,
        api_handlers,
        model_handlers,
        context_handlers,
        settings_handlers,
    ):
        dp.include_router(module.router)

    log.info("Starting polling…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        log.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())