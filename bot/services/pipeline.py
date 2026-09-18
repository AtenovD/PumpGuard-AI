from __future__ import annotations

import logging
import json
import time
from dataclasses import asdict

import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import config
from bot.services.chains import chain_label, token_url
from bot.services import agents
from bot.services.digest import synthesize_digest
from bot.services.executor import DryRunExecutor
from bot.services.decision import DecisionRecorder
from bot.services.models import Token, TokenAnalysis
from bot.services.protections import active_locks
from bot.services.reputation import ReputationBook
from bot.services.risk import RiskManager
from bot.services.storage import Position, Storage
from bot.services.webhooks import deliver_signal_webhooks

logger = logging.getLogger(__name__)

# Below this many unique buyers, or younger than this, a launch is filtered
# out before it costs a single Grok call.
CODE_FILTER_MIN_BUYERS = 5


def _code_filter(token: Token) -> str | None:
    if token.unique_buyers is not None and token.unique_buyers < config.min_unique_buyers:
        return "too_few_buyers"
    if time.time() - token.created_at < config.min_launch_age_seconds:
        return "too_young"
    return None


async def screen_token(
    session: aiohttp.ClientSession,
    storage: Storage,
    risk: RiskManager,
    reputation: ReputationBook,
    executor: DryRunExecutor,
    token: Token,
    market_snapshot: dict,
    feed_healthy: bool | None = None,
) -> TokenAnalysis | None:
    """Runs one token through the full pipeline. Returns the analysis if it was bought (dry-run).

    Whatever the outcome - filtered, abstained, rejected, blocked or bought - the
    full trail is saved as a decision record, so a skip can be explained later.
    """
    recorder = DecisionRecorder(chain=token.chain, subject=token.mint, symbol=token.symbol)
    recorder.inputs = {
        "token": {
            "mint": token.mint, "symbol": token.symbol, "creator": token.creator,
            "created_at": token.created_at, "reference_price": token.reference_price,
            "unique_buyers": token.unique_buyers, "curve": asdict(token.curve) if token.curve else None,
            "trades": token.trades,
        },
        "market_snapshot": market_snapshot,
        "feed_healthy": feed_healthy,
    }
    context = recorder.start()
    try:
        return await _screen(
            session, storage, risk, reputation, executor, token, market_snapshot, feed_healthy, recorder
        )
    except Exception as exc:
        recorder.finish("error", "error", repr(exc))
        raise
    finally:
        DecisionRecorder.stop(context)
        try:
            await storage.save_decision(
                token.chain, token.mint, token.symbol, recorder.stage, recorder.outcome,
                recorder.score, recorder.payload(),
            )
        except Exception:
            # Keeping the audit trail must never be the reason a screening fails.
            logger.exception("could not save decision record for %s", token.mint)


async def _stop(
    storage: Storage, recorder: DecisionRecorder, token: Token, stage: str, outcome: str,
    detail: str = "", score: float | None = None,
) -> None:
    await storage.log_signal(token.mint, token.symbol, score, stage, outcome, detail, token.chain)
    recorder.finish(stage, outcome, detail, score)


async def _screen(
    session: aiohttp.ClientSession,
    storage: Storage,
    risk: RiskManager,
    reputation: ReputationBook,
    executor: DryRunExecutor,
    token: Token,
    market_snapshot: dict,
    feed_healthy: bool | None,
    recorder: DecisionRecorder,
) -> TokenAnalysis | None:
    analysis = TokenAnalysis(token=token)

    reason = _code_filter(token)
    if reason:
        await _stop(storage, recorder, token, "filter", "skip", reason)
        return None

    # Protections come before any paid call: a lock means nothing is bought, so
    # there is no point spending Grok calls to score a token we cannot enter.
    locks = await active_locks(storage, feed_healthy)
    if locks:
        recorder.locks = [asdict(lock) for lock in locks]
        await _stop(storage, recorder, token, "protection", "skip", locks[0].name)
        return None

    blocked = await reputation.is_blocked(token.creator, token.chain)
    if blocked:
        await _stop(storage, recorder, token, "reputation", "skip", blocked)
        return None

    # Researcher is a free, instant DB lookup (creator history, copycat-name detection) -
    # it runs before any paid Grok call and its findings are handed to the checker later.
    analysis.researcher = await agents.run_researcher(storage, token)
    recorder.add_verdict("researcher", analysis.researcher)

    # Sequential on purpose: an abstention ends the run immediately, so one
    # unavailable agent does not burn calls on the others.
    for name, runner in (
        ("auditor", lambda: agents.run_auditor(session, token)),
        ("narrative", lambda: agents.run_narrative(session, token)),
        ("timing", lambda: agents.run_timing(session, market_snapshot)),
    ):
        verdict = await runner()
        setattr(analysis, name, verdict)
        recorder.add_verdict(name, verdict)
        if verdict.abstained:
            await _stop(storage, recorder, token, "abstain", "abstain", f"{name}:{verdict.abstain_reason}")
            return None

    weights = {"auditor": 0.4, "narrative": 0.3, "timing": 0.3}
    total = (
        analysis.auditor.score * weights["auditor"]
        + analysis.narrative.score * weights["narrative"]
        + analysis.timing.score * weights["timing"]
    )
    analysis.total_score = round(total, 4)

    if total < 0.5 or not (analysis.auditor.approve and analysis.narrative.approve and analysis.timing.approve):
        await _stop(storage, recorder, token, "scoring", "skip", "below_threshold", total)
        return None

    analysis.checker = await agents.run_checker(
        session, token, [analysis.researcher, analysis.auditor, analysis.narrative, analysis.timing]
    )
    recorder.add_verdict("checker", analysis.checker)
    if analysis.checker.abstained:
        await _stop(storage, recorder, token, "abstain", "abstain", f"checker:{analysis.checker.abstain_reason}", total)
        return None
    if not analysis.checker.approve:
        await _stop(storage, recorder, token, "checker", "skip", analysis.checker.summary, total)
        return None

    analysis.risk = await risk.evaluate(total)
    recorder.risk = asdict(analysis.risk)
    if not analysis.risk.approved:
        await _stop(storage, recorder, token, "risk", "skip", analysis.risk.reason, total)
        return None

    result = await executor.buy(token, analysis.risk.size_sol)
    if not result.ok:
        await _stop(storage, recorder, token, "executor", "skip", result.error, total)
        return None

    analysis.fill = {
        "price": result.price, "tokens": result.tokens, "fee_eth": result.fee_eth,
        "impact_pct": result.impact_pct, "model": result.model,
    }
    recorder.fill = analysis.fill
    await storage.open_position(
        Position(
            mint=token.mint,
            symbol=token.symbol,
            entry_price=result.price,
            sol_spent=analysis.risk.size_sol,
            score=total,
            creator=token.creator,
            opened_at=int(time.time()),
            status="open",
            chain=token.chain,
            token_amount=result.tokens,
        )
    )
    await storage.record_trade()
    await _stop(storage, recorder, token, "executor", "bought", result.tx_hash, total)
    return analysis


def _fill_line(analysis: TokenAnalysis) -> str:
    fill = analysis.fill
    if not fill:
        return ""
    return (
        f"Simulated fill: {fill['impact_pct']:+.1f}% price impact, "
        f"{fill['fee_eth']:.4f} ETH fee ({fill['model']} model)\n"
    )


async def broadcast_signal(
    bot: Bot, session: aiohttp.ClientSession, storage: Storage, analysis: TokenAnalysis
) -> None:
    await deliver_signal_webhooks(session, analysis, config.webhook_urls)
    if not config.alert_chat_id:
        return
    token = analysis.token
    digest = await synthesize_digest(session, analysis)
    text = (
        f"🟢 <b>{token.symbol or token.mint[:8]}</b> — score {analysis.total_score:.2f}\n"
        f"{chain_label(token.chain)}\n\n"
        f"{digest}\n\n"
        f"Position size: {analysis.risk.size_sol:.4f} ETH (dry-run)\n"
        f"{_fill_line(analysis)}"
        f"{token_url(token.chain, token.mint)}"
    )
    snapshot_id = await storage.save_analysis_snapshot(
        token.chain, token.mint, json.dumps(asdict(analysis), ensure_ascii=False)
    )
    keyboard = None
    if config.xai_oauth_client_id and config.xai_oauth_redirect_uri and config.oauth_encryption_key:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔎 Ask Grok", callback_data=f"oauth:ask:{snapshot_id}")
        ]])
    await bot.send_message(
        config.alert_chat_id, text, disable_web_page_preview=True, reply_markup=keyboard
    )
