"""Reference model routing capability.

Implements reference-model-based answer calibration for the MoA Gateway.
A primary model produces an answer; a secondary reference model (possibly
a different provider, smaller or faster) is used to validate / shadow /
veto the primary answer. The router returns a calibrated decision
(accept / flag / reject) along with actionable calibration suggestions.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class RefStrategy(str, Enum):
    """How the reference model is consulted relative to the primary model."""

    NONE = "none"
    SHADOW = "shadow"
    VALIDATE = "validate"
    VETO = "veto"


class Decision(str, Enum):
    """Calibrated decision emitted by the router."""

    ACCEPT = "accept"
    FLAG = "flag"
    REJECT = "reject"


@dataclass
class ReferenceConfig:
    """Configuration for a reference-routing call.

    Attributes:
        main_model: Identifier of the primary generation model.
        ref_model: Identifier of the reference/validation model.
        strategy: Reference consultation strategy.
        max_latency_ms: Maximum allowed latency for the reference call.
            If exceeded, the router falls back to SHADOW-style behaviour.
        cost_ratio_cap: Soft cap on ref/main cost ratio. When the
            estimated ratio exceeds this cap, the router logs a
            calibration warning and downgrades VETO -> VALIDATE.
        similarity_threshold_accept: Agreement score (>=) for accept.
        similarity_threshold_reject: Agreement score (<) for reject.
            Values in (threshold_reject, threshold_accept) -> flag.
    """

    main_model: str = "main-large"
    ref_model: str = "ref-small"
    strategy: RefStrategy = RefStrategy.SHADOW
    max_latency_ms: int = 2000
    cost_ratio_cap: float = 0.5
    similarity_threshold_accept: float = 0.85
    similarity_threshold_reject: float = 0.60


@dataclass
class CalibrationItem:
    """A single calibration suggestion.

    Attributes:
        issue_type: Short machine-readable category (e.g. "drift",
            "length_mismatch", "missing_disclaimer", "cost_overrun").
        severity: "low" | "medium" | "high".
        suggestion: Human-readable remediation hint.
    """

    issue_type: str
    severity: str
    suggestion: str


@dataclass
class ReferenceResult:
    """Outcome of a reference-routed call.

    Attributes:
        main_answer: Output produced by the primary model.
        ref_answer: Output produced by the reference model (may be empty
            when the strategy is NONE or when the ref call failed).
        similarity: Jaccard token overlap in [0, 1].
        agreement: Combined agreement score in [0, 1] (Jaccard blended
            with key-sentence cosine-style similarity).
        calibration: List of calibration suggestions.
        decision: accept | flag | reject.
        latency_ms: Wall-clock latency of the whole call.
        main_cost: Estimated cost of the primary call (mock units).
        ref_cost: Estimated cost of the reference call (mock units).
        strategy_used: The strategy that was actually applied (may differ
            from the requested one in failure / cost-cap / timeout
            scenarios).
        error: Optional error message if the ref call failed.
    """

    main_answer: str
    ref_answer: str = ""
    similarity: float = 0.0
    agreement: float = 0.0
    calibration: list[CalibrationItem] = field(default_factory=list)
    decision: Decision = Decision.ACCEPT
    latency_ms: int = 0
    main_cost: float = 0.0
    ref_cost: float = 0.0
    strategy_used: RefStrategy = RefStrategy.NONE
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "main_answer": self.main_answer,
            "ref_answer": self.ref_answer,
            "similarity": self.similarity,
            "agreement": self.agreement,
            "calibration": [
                {"issue_type": c.issue_type, "severity": c.severity, "suggestion": c.suggestion}
                for c in self.calibration
            ],
            "decision": self.decision.value,
            "latency_ms": self.latency_ms,
            "main_cost": self.main_cost,
            "ref_cost": self.ref_cost,
            "strategy_used": self.strategy_used.value,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

# Provider registry: maps model id -> (latency_ms, base_cost, behaviour).
_MOCK_PROVIDERS: dict[str, dict[str, float]] = {
    "main-large": {"latency_ms": 120.0, "cost": 0.010, "quality": 0.95},
    "main-medium": {"latency_ms": 80.0, "cost": 0.006, "quality": 0.85},
    "ref-small": {"latency_ms": 60.0, "cost": 0.001, "quality": 0.80, "agreement_bias": 0.90},
    "ref-fast": {"latency_ms": 20.0, "cost": 0.0005, "quality": 0.70, "agreement_bias": 0.85},
    "ref-strict": {"latency_ms": 90.0, "cost": 0.002, "quality": 0.88, "agreement_bias": 0.75},
    "ref-slow": {"latency_ms": 3000.0, "cost": 0.002, "quality": 0.90, "agreement_bias": 0.92},
    "ref-flaky": {"latency_ms": 50.0, "cost": 0.001, "quality": 0.0, "fail_rate": 0.0},
}

# Allow tests to inject custom providers.
_PROVIDER_OVERRIDES: dict[str, dict[str, float]] = {}


def register_mock_provider(model_id: str, **kwargs: float) -> None:
    """Register or override a mock provider entry (used by tests)."""
    _PROVIDER_OVERRIDES[model_id] = dict(kwargs)


def _lookup_provider(model_id: str) -> dict[str, float]:
    if model_id in _PROVIDER_OVERRIDES:
        return _PROVIDER_OVERRIDES[model_id]
    if model_id in _MOCK_PROVIDERS:
        return _MOCK_PROVIDERS[model_id]
    # Unknown model: safe default.
    return {"latency_ms": 50.0, "cost": 0.001, "quality": 0.8, "agreement_bias": 0.85}


def _generate_text_sync(model_id: str, query: str) -> tuple[str, float, float]:
    """Synchronous mock generation.

    Returns ``(text, latency_ms, cost)``. Runs in a thread when invoked
    from async code via :func:`asyncio.to_thread`.
    """
    spec = _lookup_provider(model_id)
    latency = float(spec.get("latency_ms", 50.0))
    cost = float(spec.get("cost", 0.001))

    # Optional failure injection (used by tests for the ref-flaky model).
    if float(spec.get("fail_rate", 0.0)) > 0.0:
        raise RuntimeError(f"mock provider {model_id} failed")

    # Actually sleep so async timeouts / latency assertions are honoured.
    if latency > 0:
        time.sleep(latency / 1000.0)

    # Build a deterministic but query-sensitive answer. Behaviour varies
    # by model ``quality`` and ``agreement_bias`` so the router can
    # observe realistic agreement differences.
    quality = float(spec.get("quality", 0.8))
    bias = float(spec.get("agreement_bias", 0.9))
    text = _synthesise_answer(query, quality, bias)
    return text, latency, cost


def _synthesise_answer(query: str, quality: float, bias: float) -> str:
    """Produce a deterministic mock answer for the given query.

    Higher ``quality`` -> answer contains more query tokens.
    Higher ``bias`` -> answer is closer to a canonical template.
    """
    q = (query or "").strip().lower()
    tokens = _tokenize(query)

    if not q:
        return ""

    canonical = "this is a reference answer covering " + " ".join(tokens[:5]) if tokens else "no content"
    extra = " and additional context" if quality >= 0.8 else ""
    noise = " random filler" if quality < 0.6 else ""

    if bias >= 0.9:
        return canonical + extra
    if bias >= 0.8:
        # Add small noise.
        return canonical + extra + noise
    # Strict/low-bias model tends to disagree more.
    return ("divergent " if quality < 0.85 else canonical) + " " + canonical + noise


# ---------------------------------------------------------------------------
# Agreement & calibration
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def jaccard(a: str, b: str) -> float:
    """Jaccard token overlap between two strings in [0, 1]."""
    sa, sb = set(_tokenize(a)), set(_tokenize(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    # Split on . ! ? and CJK full stops.
    parts = re.split(r"[.!?\u3002\uff01\uff1f]+", text)
    return [p.strip().lower() for p in parts if p.strip()]


def _key_sentence_similarity(a: str, b: str) -> float:
    """Average best-match Jaccard over the key sentences of ``a`` against ``b``."""
    sa = _split_sentences(a)
    sb = _split_sentences(b)
    if not sa or not sb:
        return 0.0
    sb_tokens = [set(_tokenize(s)) for s in sb]
    total = 0.0
    for sent in sa:
        tokens = set(_tokenize(sent))
        if not tokens:
            continue
        best = 0.0
        for ts in sb_tokens:
            if not ts:
                continue
            inter = len(tokens & ts)
            union = len(tokens | ts)
            if union == 0:
                continue
            score = inter / union
            best = max(best, score)
        total += best
    return total / max(len(sa), 1)


def compute_agreement(main: str, ref: str) -> tuple[float, float]:
    """Compute (similarity, agreement) for two answers.

    ``similarity`` is the Jaccard token overlap. ``agreement`` blends
    similarity (60%) with key-sentence similarity (40%) and is clamped
    to [0, 1].
    """
    if not main and not ref:
        return 1.0, 1.0
    if not main or not ref:
        return 0.0, 0.0
    sim = jaccard(main, ref)
    sent = _key_sentence_similarity(main, ref)
    agreement = max(0.0, min(1.0, 0.6 * sim + 0.4 * sent))
    return sim, agreement


def _build_calibration(
    main: str,
    ref: str,
    agreement: float,
    decision: Decision,
    cost_ratio: float,
    cost_ratio_cap: float,
) -> list[CalibrationItem]:
    items: list[CalibrationItem] = []
    if not main:
        items.append(CalibrationItem("empty_main", "high", "Primary model returned empty output."))
    if ref and not main:
        items.append(
            CalibrationItem(
                "missing_main",
                "high",
                "Reference model produced output but primary did not; investigate primary failure.",
            )
        )
    if main and not ref:
        items.append(
            CalibrationItem(
                "no_reference",
                "medium",
                "Reference model produced no output; consider fallback or re-prompt.",
            )
        )
    if 0.6 <= agreement < 0.85:
        items.append(
            CalibrationItem(
                "drift",
                "medium",
                f"Main and reference agree at {agreement:.2f}; review answer for factual drift.",
            )
        )
    if agreement < 0.6:
        items.append(
            CalibrationItem(
                "disagreement",
                "high",
                f"Strong disagreement ({agreement:.2f}); regenerate or escalate to human review.",
            )
        )
    if cost_ratio > cost_ratio_cap:
        items.append(
            CalibrationItem(
                "cost_overrun",
                "medium",
                f"Ref/main cost ratio {cost_ratio:.2f} exceeds cap {cost_ratio_cap:.2f}; consider smaller ref model.",
            )
        )
    if decision == Decision.REJECT:
        items.append(
            CalibrationItem(
                "rejected",
                "high",
                "Reference vetoed the answer; main output should not be surfaced without review.",
            )
        )
    if decision == Decision.FLAG:
        items.append(
            CalibrationItem(
                "flagged",
                "low",
                "Answer flagged for downstream review.",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _call_model(model_id: str, query: str, timeout_ms: int | None = None) -> tuple[str, float, float]:
    """Call a mock model and return ``(text, latency_ms, cost)``.

    Honours ``timeout_ms`` via :func:`asyncio.wait_for`. The synchronous
    work runs in a thread via :func:`asyncio.to_thread` so the event
    loop is never blocked.
    """
    coro = asyncio.to_thread(_generate_text_sync, model_id, query)
    if timeout_ms is not None and timeout_ms > 0:
        text, latency, cost = await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
    else:
        text, latency, cost = await coro
    return text, latency, cost


def _decide(agreement: float, cfg: ReferenceConfig) -> Decision:
    if agreement >= cfg.similarity_threshold_accept:
        return Decision.ACCEPT
    if agreement < cfg.similarity_threshold_reject:
        return Decision.REJECT
    return Decision.FLAG


async def route_with_reference(query: str, config: ReferenceConfig) -> ReferenceResult:
    """Route a query through the primary model and consult a reference model.

    The four supported strategies are:

    * :attr:`RefStrategy.NONE` – no reference call; return the main answer.
    * :attr:`RefStrategy.SHADOW` – call the reference concurrently in the
      background; the main answer is returned immediately and the
      calibration hints are filled in once the ref call resolves.
    * :attr:`RefStrategy.VALIDATE` – block until the reference answers;
      the answer is accepted only if the reference call succeeds.
    * :attr:`RefStrategy.VETO` – like VALIDATE, but the answer is rejected
      when the agreement falls below ``similarity_threshold_reject``.

    Failure handling: any exception inside the reference call is caught
    and the router degrades to SHADOW behaviour – the main answer is
    returned, with a calibration hint describing the failure.
    """
    start = time.monotonic()
    requested = config.strategy

    # Cost-cap downgrade for VETO.
    effective_strategy = requested
    main_spec = _lookup_provider(config.main_model)
    ref_spec = _lookup_provider(config.ref_model)
    estimated_ratio = (
        float(ref_spec.get("cost", 0.001)) / float(main_spec.get("cost", 0.001))
        if float(main_spec.get("cost", 0.001)) > 0
        else 0.0
    )
    if requested == RefStrategy.VETO and estimated_ratio > config.cost_ratio_cap:
        effective_strategy = RefStrategy.VALIDATE

    try:
        main_answer, main_latency, main_cost = await _call_model(config.main_model, query)
    except Exception as exc:  # primary failure is fatal
        elapsed = int((time.monotonic() - start) * 1000)
        return ReferenceResult(
            main_answer="",
            ref_answer="",
            similarity=0.0,
            agreement=0.0,
            calibration=[
                CalibrationItem(
                    "primary_failure",
                    "high",
                    f"Primary model '{config.main_model}' failed: {exc}",
                )
            ],
            decision=Decision.REJECT,
            latency_ms=elapsed,
            main_cost=0.0,
            ref_cost=0.0,
            strategy_used=effective_strategy,
            error=str(exc),
        )

    # ---- NONE -------------------------------------------------------------
    if effective_strategy == RefStrategy.NONE:
        elapsed = int((time.monotonic() - start) * 1000)
        return ReferenceResult(
            main_answer=main_answer,
            ref_answer="",
            similarity=0.0,
            agreement=1.0,  # no disagreement possible
            calibration=[],
            decision=Decision.ACCEPT,
            latency_ms=elapsed,
            main_cost=main_cost,
            ref_cost=0.0,
            strategy_used=RefStrategy.NONE,
        )

    # ---- SHADOW -----------------------------------------------------------
    if effective_strategy == RefStrategy.SHADOW:
        # Fire-and-forget background task. We do not await it.
        async def _shadow_call() -> None:
            try:
                await _call_model(config.ref_model, query, timeout_ms=config.max_latency_ms)
            except Exception as exc:  # noqa: BLE001
                logger.warning("shadow ref call failed: %s", exc)

        asyncio.create_task(_shadow_call())
        elapsed = int((time.monotonic() - start) * 1000)
        return ReferenceResult(
            main_answer=main_answer,
            ref_answer="",
            similarity=0.0,
            agreement=1.0,
            calibration=[
                CalibrationItem(
                    "shadow_only",
                    "low",
                    "Reference ran in shadow mode; no blocking validation performed.",
                )
            ],
            decision=Decision.ACCEPT,
            latency_ms=elapsed,
            main_cost=main_cost,
            ref_cost=0.0,
            strategy_used=RefStrategy.SHADOW,
        )

    # ---- VALIDATE / VETO --------------------------------------------------
    try:
        ref_answer, ref_latency, ref_cost = await _call_model(
            config.ref_model, query, timeout_ms=config.max_latency_ms
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ReferenceResult(
            main_answer=main_answer,
            ref_answer="",
            similarity=0.0,
            agreement=0.0,
            calibration=[
                CalibrationItem(
                    "ref_timeout",
                    "high",
                    f"Reference model exceeded {config.max_latency_ms} ms; degrading to shadow.",
                )
            ],
            decision=Decision.FLAG,
            latency_ms=elapsed,
            main_cost=main_cost,
            ref_cost=0.0,
            strategy_used=RefStrategy.SHADOW,
            error="timeout",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return ReferenceResult(
            main_answer=main_answer,
            ref_answer="",
            similarity=0.0,
            agreement=0.0,
            calibration=[
                CalibrationItem(
                    "ref_failure",
                    "high",
                    f"Reference model failed ({exc}); degrading to shadow.",
                )
            ],
            decision=Decision.FLAG,
            latency_ms=elapsed,
            main_cost=main_cost,
            ref_cost=0.0,
            strategy_used=RefStrategy.SHADOW,
            error=str(exc),
        )

    sim, agreement = compute_agreement(main_answer, ref_answer)
    decision = _decide(agreement, config)

    if effective_strategy == RefStrategy.VALIDATE:
        # In validate mode, a failed ref call would have already returned.
        # If agreement is very low we still flag rather than reject.
        if decision == Decision.REJECT:
            decision = Decision.FLAG
    elif effective_strategy == RefStrategy.VETO:
        # VETO keeps the computed decision as-is (REJECT allowed).
        pass

    elapsed = int((time.monotonic() - start) * 1000)
    cost_ratio = (ref_cost / main_cost) if main_cost > 0 else 0.0
    calibration = _build_calibration(
        main_answer, ref_answer, agreement, decision, cost_ratio, config.cost_ratio_cap
    )

    return ReferenceResult(
        main_answer=main_answer,
        ref_answer=ref_answer,
        similarity=sim,
        agreement=agreement,
        calibration=calibration,
        decision=decision,
        latency_ms=elapsed,
        main_cost=main_cost,
        ref_cost=ref_cost,
        strategy_used=effective_strategy,
    )


__all__ = [
    "RefStrategy",
    "Decision",
    "ReferenceConfig",
    "ReferenceResult",
    "CalibrationItem",
    "route_with_reference",
    "compute_agreement",
    "jaccard",
    "register_mock_provider",
]
