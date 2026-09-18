from __future__ import annotations

import statistics
import time
from dataclasses import asdict

import aiosqlite

from bot.services.storage import Storage


async def _columns(storage: Storage, table: str) -> set[str]:
    try:
        cursor = await storage.db.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in await cursor.fetchall()}
    except aiosqlite.OperationalError:
        return set()


async def _one(storage: Storage, sql: str, params: tuple = ()) -> object:
    try:
        cursor = await storage.db.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else 0
    except aiosqlite.OperationalError:
        return 0


async def read_stats(storage: Storage) -> dict[str, int | float]:
    cutoff = int(time.time()) - 86400
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return {
        "users_total": int(await _one(storage, "SELECT COUNT(*) FROM users")),
        "screened_24h": int(await _one(
            storage, "SELECT COUNT(*) FROM signals WHERE created_at >= ?", (cutoff,)
        )),
        "bought_24h": int(await _one(
            storage,
            "SELECT COUNT(*) FROM signals WHERE outcome = 'bought' AND created_at >= ?",
            (cutoff,),
        )),
        "trades_today": int(await _one(
            storage, "SELECT trades FROM daily_counters WHERE day = ?", (day,)
        )),
        "pnl_today": round(float(await _one(
            storage, "SELECT realized_pnl_sol FROM daily_counters WHERE day = ?", (day,)
        )), 4),
        "open_positions": int(await _one(
            storage, "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )),
        "blocked_creators": int(await _one(
            storage, "SELECT COUNT(*) FROM creators WHERE rugs > 0"
        )),
    }


async def read_funnel(storage: Storage, since_seconds: int) -> list[dict[str, object]]:
    cutoff = int(time.time()) - since_seconds
    try:
        cursor = await storage.db.execute(
            "SELECT stage, outcome, COUNT(*) FROM signals WHERE created_at >= ? "
            "GROUP BY stage, outcome ORDER BY stage, outcome",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except aiosqlite.OperationalError:
        rows = []
    funnel = [
        {"stage": stage, "outcome": outcome, "count": count}
        for stage, outcome, count in rows
    ]
    maximum = max((int(item["count"]) for item in funnel), default=0)
    for item in funnel:
        item["width"] = (int(item["count"]) / maximum * 100) if maximum else 0
    return funnel


async def read_backtest(storage: Storage, since_seconds: int) -> dict[str, object]:
    # Once the independent backtest PR is merged, use its canonical report for
    # day-based windows. Keep a SELECT-only fallback so this PR works on main.
    if since_seconds % 86400 == 0:
        try:
            from bot.services.backtest import run_backtest

            report = await run_backtest(storage, since_days=since_seconds // 86400)
            return asdict(report)
        except (ImportError, AttributeError, aiosqlite.OperationalError):
            pass

    cutoff = int(time.time()) - since_seconds
    try:
        cursor = await storage.db.execute(
            "SELECT stage, outcome, COUNT(*) FROM signals WHERE created_at >= ? "
            "GROUP BY stage, outcome",
            (cutoff,),
        )
        signal_rows = await cursor.fetchall()
    except aiosqlite.OperationalError:
        signal_rows = []

    total = sum(row[2] for row in signal_rows)
    bought = sum(row[2] for row in signal_rows if row[1] == "bought")
    skipped = sum(row[2] for row in signal_rows if row[1] == "skip")
    pnl_rows: list[tuple] = []
    position_columns = await _columns(storage, "positions")
    if {"exit_price", "closed_at", "close_reason"}.issubset(position_columns):
        # Prefer the net proceeds recorded since v1.1 (already after fees and
        # price impact); older rows only have an exit price.
        net = "exit_proceeds" in position_columns
        try:
            cursor = await storage.db.execute(
                "SELECT mint, symbol, entry_price, exit_price, close_reason, "
                + ("exit_proceeds, sol_spent" if net else "NULL, NULL")
                + " FROM positions WHERE status = 'closed' AND exit_price IS NOT NULL "
                "AND entry_price > 0 AND COALESCE(closed_at, opened_at) >= ?",
                (cutoff,),
            )
            pnl_rows = await cursor.fetchall()
        except aiosqlite.OperationalError:
            pnl_rows = []
    values = [
        (row[5] - row[6]) / row[6] * 100 if row[5] is not None and row[6] else
        (row[3] - row[2]) / row[2] * 100
        for row in pnl_rows
    ]
    best_row = max(zip(pnl_rows, values), key=lambda item: item[1], default=None)
    worst_row = min(zip(pnl_rows, values), key=lambda item: item[1], default=None)

    def position(item: tuple | None) -> dict[str, object] | None:
        if item is None:
            return None
        row, pnl = item
        return {"mint": row[0], "symbol": row[1], "pnl_pct": round(pnl, 2)}

    return {
        "total_signals": total,
        "total_bought": bought,
        "total_skipped": skipped,
        "win_rate": (sum(value > 0 for value in values) / len(values)) if values else 0.0,
        "avg_pnl_pct": statistics.fmean(values) if values else 0.0,
        "median_pnl_pct": statistics.median(values) if values else 0.0,
        "best_position": position(best_row),
        "worst_position": position(worst_row),
        "stop_loss_hit_rate": (
            sum(row[4] == "stop_loss" for row in pnl_rows) / len(pnl_rows)
            if pnl_rows else 0.0
        ),
    }


async def read_recent_decisions(storage: Storage, limit: int = 20, subject: str | None = None) -> list[dict[str, object]]:
    """Recent decision records with their payload parsed, or [] on a pre-v1.1 database."""
    import json

    try:
        rows = await storage.recent_decisions(limit, subject)
    except aiosqlite.OperationalError:
        return []
    for row in rows:
        try:
            row["payload"] = json.loads(str(row["payload"]))
        except ValueError:
            pass
    return rows


async def read_open_positions(storage: Storage) -> list[dict[str, object]]:
    columns = await _columns(storage, "positions")
    if not columns:
        return []
    chain_expr = "p.chain" if "chain" in columns else "'robinhood'"
    try:
        cursor = await storage.db.execute(
            f"SELECT p.mint, {chain_expr}, p.symbol, p.entry_price, p.sol_spent, p.score, "
            "s.price, s.observed_at FROM positions p LEFT JOIN price_snapshots s "
            f"ON s.mint = p.mint AND s.chain = {chain_expr} "
            "WHERE p.status = 'open' ORDER BY p.opened_at DESC"
        )
        rows = await cursor.fetchall()
    except aiosqlite.OperationalError:
        return []
    return [
        {
            "mint": row[0], "chain": row[1], "symbol": row[2], "entry_price": row[3],
            "sol_spent": row[4], "score": row[5], "current_price": row[6],
            "price_observed_at": row[7],
        }
        for row in rows
    ]
