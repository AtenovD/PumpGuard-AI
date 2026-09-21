from __future__ import annotations

import asyncio
import logging

import aiohttp

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot import __version__
from bot.config import config
from bot.handlers import admin, oauth, start
from bot.middlewares.subscription import SubscriptionMiddleware
from bot.services.chains import build_adapters
from bot.services.nft.adapter import RobinhoodNftAdapter
from bot.services.nft.scheduler import run_floor_sweep_watcher, run_nft_monitor_loop
from bot.services.reputation import ReputationBook
from bot.services.scheduler import run_monitor_loop, run_position_watcher
from bot.services.storage import Storage
from bot.services.weekly_digest import run_weekly_digest_loop
from bot.services.runtime_health import run_runtime_health_reporter


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger(__name__).info("PumpGuard AI v%s starting", __version__)

    storage = Storage(config.db_path)
    await storage.connect()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["storage"] = storage

    subscription_middleware = SubscriptionMiddleware(storage)
    dp.message.outer_middleware(subscription_middleware)
    dp.callback_query.outer_middleware(subscription_middleware)

    dp.include_router(admin.router)
    dp.include_router(oauth.router)
    dp.include_router(start.router)

    reputation = ReputationBook(storage)
    adapters = build_adapters()
    adapter_map = {adapter.chain_id: adapter for adapter in adapters}
    price_feed_tasks = [asyncio.create_task(adapter.run()) for adapter in adapters]
    monitor_task = asyncio.create_task(run_monitor_loop(bot, storage, adapters))
    watcher_task = asyncio.create_task(run_position_watcher(storage, reputation, adapter_map))
    digest_task = (
        asyncio.create_task(run_weekly_digest_loop(bot, storage, config.public_digest_chat_id))
        if config.public_digest_chat_id else None
    )

    nft_tasks: list[asyncio.Task] = []
    health_adapters = list(adapters)
    if config.nft_screener_enabled:
        nft_adapter = RobinhoodNftAdapter(config.robinhood_nft_data_url)
        async with aiohttp.ClientSession() as probe_session:
            problem = await nft_adapter.probe(probe_session)
        if problem:
            # The NFT indexer path is an assumption (see the README); refuse to
            # spin up three tasks that poll a page that is not there.
            logging.getLogger(__name__).warning(
                "NFT screener is enabled but its data source is unavailable (%s); "
                "not starting it. It will start on the next restart once the endpoint works.",
                problem,
            )
        else:
            health_adapters.append(nft_adapter)
            nft_tasks = [
                asyncio.create_task(nft_adapter.run()),
                asyncio.create_task(run_nft_monitor_loop(bot, storage, nft_adapter)),
                asyncio.create_task(run_floor_sweep_watcher(bot, storage, nft_adapter)),
            ]

    health_task = asyncio.create_task(run_runtime_health_reporter(storage, health_adapters))

    try:
        await dp.start_polling(bot)
    finally:
        for task in price_feed_tasks:
            task.cancel()
        for task in nft_tasks:
            task.cancel()
        monitor_task.cancel()
        watcher_task.cancel()
        health_task.cancel()
        if digest_task is not None:
            digest_task.cancel()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
