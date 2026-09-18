from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from bot.config import config
from bot.services.circuit_breaker import CircuitBreaker
from bot.services.decision import record_model_call

logger = logging.getLogger(__name__)

breaker = CircuitBreaker(config.grok_breaker_failure_threshold, config.grok_breaker_cooldown_seconds)


class GrokError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply, tolerating ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?", "", cleaned).rstrip("`").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise GrokError(f"no JSON object found in reply: {text[:200]!r}")
    return json.loads(cleaned[start : end + 1])


async def ask_grok(
    session: aiohttp.ClientSession,
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    """Calls Grok with a system+user prompt pair, expects a JSON object back.

    Returns None (never raises) on any failure. Callers must treat None as an
    abstention - "no opinion", never a pass - and must not let it read as a
    rejection either. The call is also written into the decision record that is
    currently being built, if there is one, so the exact input, model and answer
    behind every verdict can be inspected later.
    """
    result = await _ask_grok(session, system_prompt, user_message, model=model)
    record_model_call(label, model or config.grok_fast_model, system_prompt, user_message, result)
    return result


async def _ask_grok(
    session: aiohttp.ClientSession,
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
) -> dict[str, Any] | None:
    if not config.grok_api_key:
        logger.warning("GROK_API_KEY not set, skipping Grok call")
        return None

    if not breaker.allow():
        logger.warning("Grok circuit breaker is open — skipping call, assuming the worst")
        return None

    payload = {
        "model": model or config.grok_fast_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    headers = {"Authorization": f"Bearer {config.grok_api_key}", "Content-Type": "application/json"}

    for attempt in range(config.grok_max_retries):
        try:
            async with session.post(
                config.grok_base_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=config.grok_timeout_seconds),
            ) as resp:
                if resp.status == 429 or resp.status >= 500:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                body = await resp.json()
                content = body["choices"][0]["message"]["content"]
                result = _extract_json(content)
                breaker.record_success()
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, json.JSONDecodeError, GrokError) as exc:
            logger.warning("Grok call failed (attempt %d/%d): %s", attempt + 1, config.grok_max_retries, exc)
            await asyncio.sleep(0.5 * (attempt + 1))

    breaker.record_failure()
    return None
