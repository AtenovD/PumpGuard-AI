from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from html import escape

from aiogram import Bot

from bot.services.backtest import BacktestReport, run_backtest
from bot.services.storage import Storage

WEEK_SECONDS = 7 * 24 * 60 * 60


def _position(value: dict | None) -> str:
    if value is None:
        return "—"
    label = value.get("symbol") or str(value.get("mint", ""))[:8]
    return f"{escape(str(label))} ({float(value['pnl_pct']):+.1f}%)"


def format_weekly_digest(report: BacktestReport) -> str:
    start = datetime.fromtimestamp(report.period_start, timezone.utc).strftime("%d %b")
    end = datetime.fromtimestamp(report.period_end, timezone.utc).strftime("%d %b %Y")
    stage_text = ", ".join(
        f"{escape(stage)}: {count}" for stage, count in sorted(report.skip_by_stage.items())
    ) or "—"
    return (
        f"📈 <b>PumpGuard weekly digest</b> · {start}–{end} UTC\n\n"
        f"Signals screened: <b>{report.total_signals}</b>\n"
        f"Dry-run buys / skipped / abstained: <b>{report.total_bought}</b> / "
        f"{report.total_skipped} / {report.total_abstained}\n"
        f"Skipped by stage: {stage_text}\n\n"
        f"Closed-position win rate: <b>{report.win_rate * 100:.1f}%</b>\n"
        f"Average / median PnL: {report.avg_pnl_pct:+.1f}% / {report.median_pnl_pct:+.1f}%\n"
        f"Net PnL after costs: {report.net_pnl_eth:+.4f} ETH\n"
        f"Best: {_position(report.best_position)}\n"
        f"Worst: {_position(report.worst_position)}\n"
        f"Stop-loss hit rate: {report.stop_loss_hit_rate * 100:.1f}%\n"
        f"Sample: {escape(report.sample_verdict)}\n\n"
        "All positions are simulated. This is not financial advice."
    )


async def publish_weekly_digest(bot: Bot, storage: Storage, chat_id: str) -> BacktestReport:
    report = await run_backtest(storage, since_days=7)
    await bot.send_message(chat_id, format_weekly_digest(report))
    return report


async def run_weekly_digest_loop(
    bot: Bot, storage: Storage, chat_id: str, interval_seconds: int = WEEK_SECONDS
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await publish_weekly_digest(bot, storage, chat_id)
