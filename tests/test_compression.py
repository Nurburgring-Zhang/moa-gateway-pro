"""Compression stack tests (RTK + Caveman + fidelity + preservation).

All assertions are based on observed engine behaviour (probe-verified);
no mocks — every case exercises the real deterministic engines.
"""

from __future__ import annotations

import pytest

from moa_gateway.compression.caveman import (
    apply_rules_to_text,
    caveman_compress_text,
    load_rule_packs,
)
from moa_gateway.compression.engine import (
    DEFAULT_STACKED_PIPELINE,
    MODES,
    CompressionEngine,
    get_engine,
    reset_engine,
)
from moa_gateway.compression.fidelity import fidelity_score
from moa_gateway.compression.preservation import (
    extract_preserved_blocks,
    restore_preserved_blocks,
)
from moa_gateway.compression.rtk import (
    detect_filter,
    deduplicate_repeated_lines,
    load_filters,
    process_rtk_text,
    smart_truncate,
)
from moa_gateway.compression.stats import CompressionStats
from moa_gateway.config import CompressionConfig

SAMPLE = (
    "npm install completed successfully. There are 127 packages that were installed "
    "in about 14 seconds. Basically the operation finished and it was actually pretty "
    "fast. added 105 packages in 14s\n"
    "added 105 packages in 14s\n"
    "34 packages are looking for funding\n"
)

REPEATED = "line-alpha\n" + "line-beta\n" * 3 + "line-gamma\n"

LONG_OUTPUT = "\n".join(f"log line {i}: some diagnostic detail" for i in range(100))


def make_engine(**overrides) -> CompressionEngine:
    cfg = CompressionConfig(**overrides) if overrides else CompressionConfig()
    return CompressionEngine(config=cfg)


# ---------- A. mode matrix (compress_text) ----------

def test_modes_tuple_complete():
    assert MODES == ("off", "lite", "standard", "aggressive", "ultra", "rtk", "stacked")


def test_off_passthrough():
    out = make_engine().compress_text(SAMPLE, mode="off")
    assert out["compressed"] is False
    assert out["text"] == SAMPLE
    assert out["original_chars"] == out["compressed_chars"] == len(SAMPLE)


def test_standard_reduces_filler():
    out = make_engine().compress_text(SAMPLE, mode="standard")
    assert out["compressed"] is True
    assert out["compressed_chars"] < out["original_chars"]
    assert "caveman-lite" in out["techniques_used"]
    assert "Basically the" not in out["text"]


def test_standard_keeps_numbers():
    out = make_engine().compress_text(SAMPLE, mode="standard")
    for token in ("127", "105", "14s"):
        assert token in out["text"], f"critical number {token} dropped"


def test_aggressive_fidelity_revert_on_short_sample():
    out = make_engine().compress_text(SAMPLE, mode="aggressive")
    assert out["compressed"] is False
    assert out["fidelity_score"] == 1.0
    assert out["text"] == SAMPLE


def test_stacked_pipeline_engines():
    out = make_engine().compress_text(SAMPLE, mode="stacked")
    assert out["techniques_used"] == ["rtk-filter", "caveman-full"]


def test_stacked_reduces():
    out = make_engine().compress_text(SAMPLE, mode="stacked")
    assert out["compressed"] is True
    assert out["compressed_chars"] < out["original_chars"]


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown compression mode"):
        make_engine().compress_text(SAMPLE, mode="bogus")


def test_result_shape():
    out = make_engine().compress_text(SAMPLE, mode="standard")
    for key in ("text", "compressed", "mode", "fidelity_score", "original_chars", "compressed_chars"):
        assert key in out


def test_compressed_chars_consistent():
    out = make_engine().compress_text(SAMPLE, mode="stacked")
    assert len(out["text"]) == out["compressed_chars"]


def test_default_stacked_pipeline_shape():
    engines = [step["engine"] for step in DEFAULT_STACKED_PIPELINE]
    assert engines == ["rtk", "caveman"]


def test_singleton_engine_reset():
    reset_engine()
    first = get_engine()
    assert get_engine() is first
    reset_engine()
    assert get_engine() is not first


# ---------- B. compress_body ----------

def _body(text: str, extra: dict | None = None) -> dict:
    msg: dict = {"role": "user", "content": text}
    if extra:
        msg.update(extra)
    return {"messages": [msg], "model": "test-model"}


def test_body_off_no_change():
    body = _body(SAMPLE)
    outcome = make_engine().compress_body(body, mode="off")
    assert outcome.compressed is False
    assert outcome.body is body


def test_body_disabled_no_change():
    body = _body(SAMPLE)
    outcome = CompressionEngine(config=CompressionConfig(enabled=False)).compress_body(body)
    assert outcome.compressed is False


def test_body_empty_messages_no_change():
    outcome = make_engine().compress_body({"messages": []}, mode="standard")
    assert outcome.compressed is False


def test_body_missing_messages_no_change():
    outcome = make_engine().compress_body({"model": "m"}, mode="standard")
    assert outcome.compressed is False


def test_body_unknown_mode_raises():
    with pytest.raises(ValueError):
        make_engine().compress_body(_body(SAMPLE), mode="nope")


def test_body_compressed_shortens():
    outcome = make_engine().compress_body(_body(SAMPLE * 3), mode="stacked")
    assert outcome.compressed is True
    after = sum(len(str(m["content"])) for m in outcome.body["messages"])
    assert after < len(SAMPLE) * 3


def test_body_cache_control_protected():
    marker = {"role": "system", "content": SAMPLE, "cache_control": {"type": "ephemeral"}}
    body = {"messages": [marker, {"role": "user", "content": SAMPLE}], "model": "m"}
    outcome = make_engine().compress_body(body, mode="stacked")
    untouched = outcome.body["messages"][0]
    assert untouched["content"] == SAMPLE
    assert "cache_control" in untouched


def test_body_max_input_chars_skip():
    huge = "x" * 1_000_001
    outcome = make_engine().compress_body(_body(huge), mode="stacked")
    assert outcome.compressed is False


def test_outcome_to_dict_shape():
    outcome = make_engine().compress_body(_body(SAMPLE), mode="standard")
    payload = outcome.to_dict()
    for key in ("compressed", "mode", "engines", "techniques_used", "rules_applied", "fidelity", "stats"):
        assert key in payload
    assert set(payload["fidelity"]) == {"passed", "reverted_messages", "score"}


# ---------- C. caveman ----------

def test_rule_packs_load():
    packs = load_rule_packs()
    assert isinstance(packs, list) and len(packs) >= 20
    names = {r.name for r in packs}
    assert "background_removal" in names
    assert "intent_clarification" in names


def test_apply_rules_removes_background():
    rules = load_rule_packs()
    text, applied = apply_rules_to_text(
        "As you may know, the build fails on CI.", rules
    )
    assert "As you may know" not in text
    assert "build fails on CI." in text
    assert "background_removal" in applied


def test_apply_rules_intent_replacement():
    rules = load_rule_packs()
    text, _applied = apply_rules_to_text(
        "What I'm trying to do is parse the log file.", rules
    )
    assert text.startswith("Goal:")
    assert "parse log file" in text


def test_caveman_compress_text_filler():
    text, applied, saved = caveman_compress_text(
        "Basically, the operation finished and it was actually pretty fast.", "user"
    )
    assert saved >= 0
    assert isinstance(applied, list)
    assert len(text) <= len("Basically, the operation finished and it was actually pretty fast.")


def test_caveman_min_length_noop():
    text, applied, saved = caveman_compress_text("short", "user")
    assert text == "short"
    assert applied == []
    assert saved == 0


def test_caveman_rules_reported():
    _text, applied, _saved = caveman_compress_text(
        "As you may know, the deployment pipeline for this service is really slow.",
        "user",
    )
    assert "background_removal" in applied


# ---------- D. rtk ----------

def test_detect_filter_by_content():
    filt = detect_filter(SAMPLE)
    assert filt is not None
    assert filt.id


def test_load_filters_large_library():
    filters = load_filters()
    assert len(filters) >= 50


def test_dedup_threshold_three():
    text, removed = deduplicate_repeated_lines(REPEATED)
    assert removed >= 2
    assert text.count("line-beta") == 1
    assert "line-alpha" in text and "line-gamma" in text


def test_dedup_below_threshold_noop():
    twice = "dup\n" + "dup\n" + "unique\n"
    text, removed = deduplicate_repeated_lines(twice, threshold=3)
    assert removed == 0
    assert text == twice


def test_smart_truncate_lines():
    text, truncated, dropped = smart_truncate(LONG_OUTPUT, max_lines=10)
    assert truncated is True
    assert dropped > 0
    assert len(text.splitlines()) < 100
    assert "log line 0:" in text
    assert "log line 99:" in text


def test_smart_truncate_noop_when_unlimited():
    text, truncated, dropped = smart_truncate(LONG_OUTPUT)
    assert truncated is False
    assert dropped == 0
    assert text == LONG_OUTPUT


def test_process_rtk_result_fields():
    result = process_rtk_text(SAMPLE, command="npm install")
    assert hasattr(result, "text")
    assert hasattr(result, "compressed")
    assert hasattr(result, "filter_id")
    assert result.filter_id == "npm-install"


def test_process_rtk_empty_noop():
    result = process_rtk_text("   \n  ")
    assert result.compressed is False


# ---------- E. fidelity ----------

def test_fidelity_identical_is_one():
    assert fidelity_score("connect to 10.0.0.1:5432", "connect to 10.0.0.1:5432") == 1.0


def test_fidelity_critical_drop():
    score = fidelity_score("connect to 10.0.0.1 port 5432", "connect to host")
    assert score < 0.5


def test_fidelity_scale_bounded():
    assert 0.0 <= fidelity_score("abc 123", "xyz") <= 1.0


# ---------- F. preservation ----------

DOC = "before text\n```py\ncode_line = 1\n```\nafter text"


def test_extract_restore_roundtrip():
    tombstoned, blocks = extract_preserved_blocks(DOC)
    assert blocks, "expected fenced code block to be preserved"
    assert restore_preserved_blocks(tombstoned, blocks) == DOC


def test_extract_tombstones_replaced():
    tombstoned, blocks = extract_preserved_blocks(DOC)
    assert "code_line = 1" not in tombstoned
    assert any(b.content == "code_line = 1\n" or "code_line" in b.content for b in blocks)


# ---------- G. stats ----------

def test_stats_store_accumulates_modes():
    from moa_gateway.compression.stats import CompressionStatsStore

    store = CompressionStatsStore()
    store.record(mode="stacked", original_chars=100, compressed_chars=80, compressed=True)
    store.record(mode="stacked", original_chars=200, compressed_chars=100, compressed=True)
    snapshot = store.snapshot()
    stacked = snapshot["modes"]["stacked"]
    assert stacked["calls"] == 2
    assert stacked["original_chars"] == 300
    assert stacked["compressed_chars"] == 180


def test_stats_dataclass_fields():
    stats = CompressionStats(
        original_tokens=100, compressed_tokens=60, savings_percent=40.0
    )
    payload = stats.to_dict()
    assert payload["saved_tokens"] == 40
    assert payload["mode"] == "off"
