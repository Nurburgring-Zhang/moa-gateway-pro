"""Free-tier model catalog: loading, validation, pool dedup, regime traits.

Real implementation ported from OmniRoute
(https://github.com/diegosouzapw/OmniRoute, MIT License) —
``open-sse/config/freeModelCatalog.ts`` (+ ``freeModelCatalog.data.ts``,
the 456-entry curated baseline bundled under ``data/``):

* ``regime`` is the catalog's ``freeType`` field. OmniRoute classifies every
  regime through ``FREE_REGIME_TRAITS`` (grants free access? which totals
  bucket? no-auth shortcut?) — the same exhaustive table is ported here.
* ``poolKey`` groups models that share ONE quota pool (e.g. one provider's
  free tier covering several model ids). Pool-deduplicated aggregation keeps
  exactly one representative per pool (highest documented allowance), and
  ``deduped_sum`` counts each shared pool once (max within the pool) — the
  exact semantics of OmniRoute ``computeFreeModelTotals``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BUNDLED_CATALOG = Path(__file__).resolve().parent / "data" / "free_model_catalog.json"

EXPECTED_ENTRY_COUNT = 456

#: The seven free-tier regimes (catalog ``freeType`` values).
REGIMES = (
    "recurring-daily",
    "recurring-monthly",
    "recurring-credit",
    "recurring-uncapped",
    "one-time-initial",
    "keyless",
    "discontinued",
)

TOS_VERDICTS = ("ok", "caution", "ambiguous", "avoid", "unknown")

_REQUIRED_FIELDS = (
    "provider",
    "modelId",
    "displayName",
    "monthlyTokens",
    "creditTokens",
    "freeType",
    "poolKey",
    "tos",
)

#: Which totals figure each regime's allowance belongs to (exhaustive table —
#: ported verbatim from OmniRoute ``FREE_REGIME_TRAITS``).
FREE_REGIME_TRAITS: dict[str, dict[str, Any]] = {
    "recurring-daily": {
        "grants_free_access": True,
        "token_bucket": "steady-monthly",
        "allows_no_auth_shortcut": False,
    },
    "recurring-monthly": {
        "grants_free_access": True,
        "token_bucket": "steady-monthly",
        "allows_no_auth_shortcut": False,
    },
    "recurring-credit": {
        "grants_free_access": True,
        "token_bucket": "recurring-credit",
        "allows_no_auth_shortcut": False,
    },
    "recurring-uncapped": {
        "grants_free_access": True,
        "token_bucket": "uncapped",
        "allows_no_auth_shortcut": False,
    },
    "one-time-initial": {
        "grants_free_access": True,
        "token_bucket": "one-time-credit",
        "allows_no_auth_shortcut": False,
    },
    "keyless": {
        "grants_free_access": True,
        "token_bucket": "steady-monthly",
        "allows_no_auth_shortcut": True,
    },
    "discontinued": {
        "grants_free_access": False,
        "token_bucket": "none",
        "allows_no_auth_shortcut": False,
    },
}

#: Deposit-unlock boosts (ported ``FREE_TIER_BOOSTS``): a one-time top-up that
#: permanently raises a pool's free quota; reported separately, never summed
#: into the steady headline.
FREE_TIER_BOOSTS: dict[str, dict[str, Any]] = {
    "openrouter-free": {
        "provider": "openrouter",
        "boostMonthlyTokens": 24_000_000,
        "note": (
            "A one-time $10 lifetime top-up raises the free pool from 50 to "
            "1000 requests/day (~24M tokens/month)."
        ),
    },
}


class CatalogValidationError(ValueError):
    """Raised when the bundled catalog fails schema/integrity validation."""


def _fmt(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    return f"{round(n / 1e6)}M"


def _validate_entry(entry: Any, index: int) -> None:
    if not isinstance(entry, dict):
        raise CatalogValidationError(f"entry {index} is not an object")
    for field in _REQUIRED_FIELDS:
        if field not in entry:
            raise CatalogValidationError(f"entry {index} missing field '{field}'")
    if entry["freeType"] not in REGIMES:
        raise CatalogValidationError(
            f"entry {index} has unknown regime {entry['freeType']!r}"
        )
    if entry["tos"] not in TOS_VERDICTS:
        raise CatalogValidationError(
            f"entry {index} has unknown tos verdict {entry['tos']!r}"
        )
    for numeric in ("monthlyTokens", "creditTokens"):
        value = entry[numeric]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CatalogValidationError(
                f"entry {index} field '{numeric}' must be a non-negative integer"
            )
    for text in ("provider", "modelId", "displayName"):
        if not isinstance(entry[text], str) or not entry[text]:
            raise CatalogValidationError(
                f"entry {index} field '{text}' must be a non-empty string"
            )
    if entry["poolKey"] is not None and not isinstance(entry["poolKey"], str):
        raise CatalogValidationError(f"entry {index} field 'poolKey' must be string or null")


@dataclass(frozen=True)
class CatalogTotals:
    steady_recurring_tokens: int
    steady_with_recurring_credits_tokens: int
    first_month_realistic_tokens: int
    boost_monthly_tokens: int
    uncapped_providers: list[str]
    model_count: int
    pool_count: int
    headline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "steadyRecurringTokens": self.steady_recurring_tokens,
            "steadyWithRecurringCreditsTokens": self.steady_with_recurring_credits_tokens,
            "firstMonthRealisticTokens": self.first_month_realistic_tokens,
            "boostMonthlyTokens": self.boost_monthly_tokens,
            "uncappedProviders": list(self.uncapped_providers),
            "modelCount": self.model_count,
            "poolCount": self.pool_count,
            "headline": self.headline,
        }


class FreeTierCatalog:
    """Validated, queryable view over the 456-entry free-model catalog."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.curated_at = str(raw.get("curatedAt", ""))
        self.source = raw.get("source") or {}
        declared = raw.get("entryCount")
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise CatalogValidationError("catalog 'entries' must be a list")
        if declared != len(entries):
            raise CatalogValidationError(
                f"entryCount {declared!r} does not match entries length {len(entries)}"
            )
        if len(entries) != EXPECTED_ENTRY_COUNT:
            raise CatalogValidationError(
                f"catalog incomplete: expected {EXPECTED_ENTRY_COUNT} entries, "
                f"found {len(entries)}"
            )
        for index, entry in enumerate(entries):
            _validate_entry(entry, index)
        keys = {(e["provider"], e["modelId"]) for e in entries}
        if len(keys) != len(entries):
            raise CatalogValidationError("duplicate provider:modelId pairs in catalog")

        self.entries: list[dict[str, Any]] = [dict(e) for e in entries]
        self._by_key: dict[str, dict[str, Any]] = {
            self.entry_key(e): e for e in self.entries
        }

    # ---------- lookups ----------

    @staticmethod
    def entry_key(entry: dict[str, Any]) -> str:
        return f"{entry['provider']}:{entry['modelId']}"

    def get(self, key: str) -> dict[str, Any] | None:
        return self._by_key.get(key)

    def providers(self) -> list[str]:
        return sorted({e["provider"] for e in self.entries})

    def regimes(self) -> list[str]:
        return list(REGIMES)

    # ---------- regime traits ----------

    @staticmethod
    def regime_traits(regime: str) -> dict[str, Any]:
        if regime not in FREE_REGIME_TRAITS:
            raise CatalogValidationError(f"unknown regime {regime!r}")
        return dict(FREE_REGIME_TRAITS[regime])

    @staticmethod
    def grants_free_access(regime: str) -> bool:
        return bool(FREE_REGIME_TRAITS[regime]["grants_free_access"])

    @staticmethod
    def regimes_in_bucket(bucket: str) -> list[str]:
        return [
            regime
            for regime, traits in FREE_REGIME_TRAITS.items()
            if traits["token_bucket"] == bucket
        ]

    # ---------- querying ----------

    def query(
        self,
        provider: str | None = None,
        regime: str | None = None,
        q: str | None = None,
        tos: str | None = None,
        exclude_tos_avoid: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Filter + paginate; sorted by monthlyTokens desc, then key asc."""
        if regime is not None and regime not in REGIMES:
            raise CatalogValidationError(f"unknown regime {regime!r}")
        if tos is not None and tos not in TOS_VERDICTS:
            raise CatalogValidationError(f"unknown tos verdict {tos!r}")
        if page < 1:
            page = 1
        if not 1 <= page_size <= 200:
            raise CatalogValidationError("page_size must be within 1..200")

        needle = q.lower().strip() if q else ""
        filtered = []
        for entry in self.entries:
            if provider and entry["provider"] != provider:
                continue
            if regime and entry["freeType"] != regime:
                continue
            if tos and entry["tos"] != tos:
                continue
            if exclude_tos_avoid and entry["tos"] == "avoid":
                continue
            if needle:
                haystack = " ".join(
                    (entry["provider"], entry["modelId"], entry["displayName"])
                ).lower()
                if needle not in haystack:
                    continue
            filtered.append(entry)

        filtered.sort(key=lambda e: (-e["monthlyTokens"], self.entry_key(e)))
        total = len(filtered)
        start = (page - 1) * page_size
        page_items = filtered[start : start + page_size]
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": page_items,
        }

    # ---------- pool dedup ----------

    def pool_representatives(self) -> list[dict[str, Any]]:
        """One representative per poolKey; un-pooled entries stand alone.

        The representative is the pool member with the highest documented
        allowance (monthlyTokens + creditTokens); ties break on modelId so
        the selection is deterministic.
        """
        best: dict[str, dict[str, Any]] = {}
        loose: list[dict[str, Any]] = []
        for entry in self.entries:
            pool = entry.get("poolKey")
            if not pool:
                loose.append(entry)
                continue
            allowance = entry["monthlyTokens"] + entry["creditTokens"]
            current = best.get(pool)
            if current is None:
                best[pool] = entry
                continue
            current_allowance = current["monthlyTokens"] + current["creditTokens"]
            if allowance > current_allowance or (
                allowance == current_allowance
                and entry["modelId"] < current["modelId"]
            ):
                best[pool] = entry
        representatives = sorted(best.values(), key=lambda e: self.entry_key(e))
        representatives.sort(key=lambda e: (-e["monthlyTokens"], self.entry_key(e)))
        return representatives + sorted(loose, key=lambda e: self.entry_key(e))

    def pools(self) -> dict[str, list[str]]:
        """poolKey -> member keys (only pools with a poolKey)."""
        grouped: dict[str, list[str]] = {}
        for entry in self.entries:
            pool = entry.get("poolKey")
            if pool:
                grouped.setdefault(pool, []).append(self.entry_key(entry))
        return {pool: sorted(keys) for pool, keys in sorted(grouped.items())}

    # ---------- totals (ported computeFreeModelTotals) ----------

    @staticmethod
    def _deduped_sum(
        models: list[dict[str, Any]],
        pick: str,
        include_regimes: frozenset[str],
    ) -> int:
        """Shared pools count once (max within the pool); null poolKey counts alone."""
        pool_max: dict[str, int] = {}
        loose = 0
        for entry in models:
            if entry["freeType"] not in include_regimes:
                continue
            value = int(entry[pick])
            pool = entry.get("poolKey")
            if pool:
                pool_max[pool] = max(pool_max.get(pool, 0), value)
            else:
                loose += value
        return loose + sum(pool_max.values())

    def compute_totals(self, exclude_tos_avoid: bool = False) -> CatalogTotals:
        models = [
            e
            for e in self.entries
            if not (exclude_tos_avoid and e["tos"] == "avoid")
        ]

        steady_monthly = frozenset(self.regimes_in_bucket("steady-monthly"))
        recurring_credit = frozenset(self.regimes_in_bucket("recurring-credit"))
        one_time_credit = frozenset(self.regimes_in_bucket("one-time-credit"))
        uncapped = frozenset(self.regimes_in_bucket("uncapped"))

        steady = self._deduped_sum(models, "monthlyTokens", steady_monthly)
        recurring_credits = self._deduped_sum(models, "creditTokens", recurring_credit)
        one_time_credits = self._deduped_sum(models, "creditTokens", one_time_credit)

        steady_with_credits = steady + recurring_credits
        first_month = steady_with_credits + one_time_credits

        live_pools = {
            e["poolKey"]
            for e in models
            if e["freeType"] in steady_monthly and e.get("poolKey")
        }
        pool_count = len(live_pools)
        boost = sum(
            info["boostMonthlyTokens"]
            for pool, info in FREE_TIER_BOOSTS.items()
            if pool in live_pools
        )

        uncapped_providers = sorted(
            {e["provider"] for e in models if e["freeType"] in uncapped}
        )

        headline = (
            f"~{_fmt(steady)} documented free tokens/month (steady), "
            f"up to ~{_fmt(first_month)} in your first month with signup credits"
        )
        return CatalogTotals(
            steady_recurring_tokens=steady,
            steady_with_recurring_credits_tokens=steady_with_credits,
            first_month_realistic_tokens=first_month,
            boost_monthly_tokens=boost,
            uncapped_providers=uncapped_providers,
            model_count=len(models),
            pool_count=pool_count,
            headline=headline,
        )


def load_catalog(path: str | Path | None = None) -> FreeTierCatalog:
    """Load + fully validate the catalog (schema + 456-entry integrity)."""
    catalog_path = Path(path) if path else _BUNDLED_CATALOG
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogValidationError(f"catalog not found: {catalog_path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(f"catalog is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogValidationError("catalog root must be an object")
    catalog = FreeTierCatalog(raw)
    logger.info(
        "free-tier catalog loaded: %d entries, curated %s",
        len(catalog.entries),
        catalog.curated_at,
    )
    return catalog


_lock = threading.Lock()
_cached: tuple[str, FreeTierCatalog] | None = None


def get_catalog() -> FreeTierCatalog:
    """Process-wide catalog (honors ``FreeTiersConfig.catalog_path``)."""
    global _cached
    from ..config import get_settings

    configured = get_settings().free_tiers.catalog_path or ""
    with _lock:
        if _cached is None or _cached[0] != configured:
            _cached = (configured, load_catalog(configured or None))
        return _cached[1]


def reset_catalog() -> None:
    """Drop the cached instance (tests / config reload)."""
    global _cached
    with _lock:
        _cached = None
