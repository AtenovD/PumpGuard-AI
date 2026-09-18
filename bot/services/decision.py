"""Decision records: everything behind one screening decision, kept together.

Before v1.1 a token that was rejected left a single `signals` row (stage,
outcome, reason). The agent verdicts, the data they were shown and the model
that produced them were gone, so there was no way to ask "why did we skip
this?" or to check afterwards whether a rejected token turned out to be a
mistake. A `DecisionRecorder` collects that trail while the pipeline runs and
`Storage.save_decision` persists it for *every* outcome, not just the passes.

The recorder travels in a context variable so `ask_grok` can attach the exact
prompt hash, model, input and parsed output to whatever decision is in flight
without every agent function growing a parameter.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

_current: contextvars.ContextVar["DecisionRecorder | None"] = contextvars.ContextVar(
    "decision_recorder", default=None
)


@dataclass
class DecisionRecorder:
    chain: str
    subject: str
    symbol: str | None = None
    started_at: float = field(default_factory=time.time)
    calls: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    verdicts: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] | None = None
    fill: dict[str, Any] | None = None
    locks: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "unknown"
    stage: str = "unknown"
    detail: str = ""
    score: float | None = None

    def start(self) -> contextvars.Token:
        return _current.set(self)

    @staticmethod
    def stop(token: contextvars.Token) -> None:
        _current.reset(token)

    def add_stage(self, stage: str, outcome: str, detail: str = "") -> None:
        self.stages.append({"stage": stage, "outcome": outcome, "detail": detail, "at": time.time()})

    def add_verdict(self, role: str, verdict: Any) -> None:
        # Keyed by the pipeline role rather than whatever the verdict calls
        # itself, so the record is stable however a verdict was produced.
        self.verdicts[role] = asdict(verdict)

    def finish(self, stage: str, outcome: str, detail: str = "", score: float | None = None) -> None:
        self.stage, self.outcome, self.detail, self.score = stage, outcome, detail, score
        self.add_stage(stage, outcome, detail)

    def payload(self) -> str:
        return json.dumps(
            {
                "chain": self.chain,
                "subject": self.subject,
                "symbol": self.symbol,
                "outcome": self.outcome,
                "stage": self.stage,
                "detail": self.detail,
                "score": self.score,
                "inputs": self.inputs,
                "verdicts": self.verdicts,
                "risk": self.risk,
                "fill": self.fill,
                "locks": self.locks,
                "stages": self.stages,
                "model_calls": self.calls,
                "elapsed_seconds": round(time.time() - self.started_at, 3),
            },
            ensure_ascii=False,
            default=str,
        )


def record_model_call(
    label: str | None,
    model: str,
    system_prompt: str,
    user_message: str,
    response: dict[str, Any] | None,
) -> None:
    """Called by `ask_grok`; a no-op when no decision is being recorded."""
    recorder = _current.get()
    if recorder is None:
        return
    recorder.calls.append(
        {
            "agent": label,
            "model": model,
            "prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest()[:16],
            "input": user_message,
            "response": response,
            "answered": response is not None,
        }
    )
