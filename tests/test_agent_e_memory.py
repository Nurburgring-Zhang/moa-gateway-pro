"""tests/test_agent_e_memory.py — M10 cross-session memory layer (MemoraX port).

Covers moa_gateway/memory/* and the M10 endpoints of
moa_gateway/routes/memory.py (ported from MemoraX Code,
https://github.com/memorax-ai/memorax-code, MIT license).

The app is self-built per the frozen architecture contract:
    app = FastAPI(); app.include_router(moa_gateway.routes.memory.router)
so these tests never depend on moa_gateway/server.py or routes/__init__.py.

Zero test doubles on the real path: the dense channel really calls the
gateway-internal embedding capability (MockEmbeddingProvider — the gateway's
own deterministic built-in, not a test fake), persistence is real SQLite in
the per-test DATA_DIR, and PII redaction runs the production regexes.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.config import Settings
from moa_gateway.memory.classifier import (
    CORE,
    EPISODIC,
    PROCEDURAL,
    SEMANTIC,
    UNCLASSIFIED,
    classify_memory_type,
    normalize_memory_type,
)
from moa_gateway.memory.hook_protocol import (
    MEMORY_HOOK_COMMAND_VERSION,
    parse_skill_reminder_command,
    parse_turn_start_command,
    parse_writeback_command,
    turn_correlation_id,
)
from moa_gateway.memory.redaction import has_meaningful_text, redact_text
from moa_gateway.memory.retrieval import (
    _sparse_scores,
    hybrid_recall,
    render_memories_xml,
    tokenize,
)
from moa_gateway.memory.scope import effective_user_id, resolve_memory_scope
from moa_gateway.memory.store import content_hash, get_memory_store, reset_memory_store
from moa_gateway.memory.service import get_memory_service, reset_memory_service
from moa_gateway.memory.vectorizer import (
    DenseVectorizer,
    char_ngram_vector,
    cosine_similarity,
)
from moa_gateway.memory.writeback import (
    CHUNK_GROUP_PREFIX,
    build_transcript,
    chunk_overlap_chars,
    chunk_text,
    enqueue_writeback,
    extract_turn_messages,
    flush_buffer,
    sweep_expired_buffers,
    turn_idempotency_key,
)

API_KEY = "mem-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
BASE_USER = "yaml-config"  # name derived by auth for yaml gateway keys


# ============ fixtures ============


@pytest.fixture(autouse=True)
def _reset_memory_singletons():
    """Memory store/service cache per-process; DATA_DIR is already per-test."""
    reset_memory_store()
    reset_memory_service()
    yield
    reset_memory_store()
    reset_memory_service()


@pytest.fixture
def memory_settings(monkeypatch):
    """Isolated settings with retrieval + writeback enabled for pipeline tests."""
    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        memory={
            "retrieval_enabled": True,
            "writeback_enabled": True,
        },
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


@pytest.fixture
def app(memory_settings):
    from moa_gateway.routes.memory import router

    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://memory.test") as ac:
        yield ac


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "demo-repo"
    ws.mkdir()
    return ws


def _cfg(**overrides):
    from moa_gateway.config import MemoryConfig

    return MemoryConfig(**overrides)


def _scope(workspace, base_user=BASE_USER):
    scope = resolve_memory_scope(workspace, base_user)
    assert scope is not None
    return scope


# ============ classifier ============


def test_classifier_procedural():
    text = "How to deploy: run make release, then push the docker image to the registry."
    assert classify_memory_type(text) == PROCEDURAL


def test_classifier_core_preference():
    text = "User preference: always answer in Chinese and prefer concise answers."
    assert classify_memory_type(text) == CORE


def test_classifier_episodic():
    text = "Yesterday we debugged the failing CI pipeline together and fixed the flaky test."
    assert classify_memory_type(text) == EPISODIC


def test_classifier_semantic():
    text = "The gateway is a FastAPI application; Python is the primary language."
    assert classify_memory_type(text) == SEMANTIC


def test_classifier_unclassified_fallback():
    assert classify_memory_type("lorem ipsum dolor sit amet") == UNCLASSIFIED


def test_normalize_memory_type():
    assert normalize_memory_type("CORE") == CORE
    assert normalize_memory_type(" procedural ") == PROCEDURAL
    assert normalize_memory_type("nonsense") == UNCLASSIFIED


# ============ scope ============


def test_scope_effective_user_id_format():
    assert effective_user_id("alice", "demo-repo") == "alice@demo-repo"


def test_scope_local_directory(workspace):
    scope = _scope(workspace)
    assert scope.effective_user_id == f"{BASE_USER}@demo-repo"
    assert scope.scope_kind == "local-directory"


def test_scope_git_root_detection(tmp_path):
    repo = tmp_path / "gitproj"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    scope = resolve_memory_scope(sub, BASE_USER)
    assert scope is not None
    assert scope.scope_kind == "git-repository"
    assert scope.repository_slug == "gitproj"
    assert scope.bound_workspace_root == str(repo)


def test_scope_fail_closed_empty_base_user(workspace):
    assert resolve_memory_scope(workspace, "") is None
    assert resolve_memory_scope(workspace, "   ") is None


def test_scope_projectless_when_no_workspace():
    scope = resolve_memory_scope(None, BASE_USER)
    assert scope is not None
    assert scope.effective_user_id.startswith(f"{BASE_USER}@")
    assert scope.identity_source == "projectless"


# ============ hook protocol: fail-closed whitelists ============


def _codex_turn_start(**extra):
    return {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "prompt": "how do I deploy?",
        "cwd": "/tmp/x",
        "workspaceKind": "repo",
        "transcriptPath": "/tmp/t.jsonl",
        **extra,
    }


def test_turn_start_valid_codex():
    ok, command = parse_turn_start_command(_codex_turn_start())
    assert ok is True
    assert command["prompt"] == "how do I deploy?"
    assert turn_correlation_id(command) == "turn-1"


def test_turn_start_rejects_unknown_key_fail_closed():
    body = _codex_turn_start(evilField="x")
    ok, error = parse_turn_start_command(body)
    assert ok is False
    assert "invalid" in str(error).lower()


def test_turn_start_rejects_wrong_version():
    body = _codex_turn_start()
    body["version"] = 2
    ok, _ = parse_turn_start_command(body)
    assert ok is False


def test_turn_start_rejects_unknown_client():
    body = _codex_turn_start()
    body["client"] = "evil-client"
    ok, _ = parse_turn_start_command(body)
    assert ok is False


def test_turn_start_rejects_missing_prompt():
    body = _codex_turn_start()
    del body["prompt"]
    ok, _ = parse_turn_start_command(body)
    assert ok is False


def test_turn_start_non_dict_rejected():
    ok, _ = parse_turn_start_command(["not", "a", "dict"])
    assert ok is False


def test_writeback_valid_claude_code():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "claude-code",
        "sessionId": "sess-2",
        "promptId": "p-9",
        "lastAssistantMessage": "Run `make release` to deploy.",
        "transcriptPath": "/tmp/cc.jsonl",
        "cwd": "/tmp/x",
    }
    ok, command = parse_writeback_command(body)
    assert ok is True
    assert turn_correlation_id(command) == "p-9"
    messages = extract_turn_messages(command)
    assert messages == [{"role": "assistant", "content": "Run `make release` to deploy."}]


def test_writeback_rejects_unknown_key_fail_closed():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "claude-code",
        "sessionId": "sess-2",
        "promptId": "p-9",
        "lastAssistantMessage": "hi",
        "transcriptPath": "/tmp/cc.jsonl",
        "unexpected": True,
    }
    ok, _ = parse_writeback_command(body)
    assert ok is False


def test_writeback_opencode_messages_shape():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "opencode",
        "sessionId": "sess-3",
        "userMessageId": "u-1",
        "assistantMessageId": "a-1",
        "messages": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "hacker", "content": "bad role dropped"},
        ],
    }
    ok, command = parse_writeback_command(body)
    assert ok is True
    messages = extract_turn_messages(command)
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_writeback_dsh_full_shape():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "dsh",
        "sessionId": "sess-4",
        "turn": 7,
        "startSeq": 0,
        "endSeq": 3,
        "cwd": "/tmp/dsh",
        "sessionHeader": {"id": "sess-4"},
        "events": [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi there"},
        ],
    }
    ok, command = parse_writeback_command(body)
    assert ok is True
    assert turn_correlation_id(command) == "7"
    assert extract_turn_messages(command) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_writeback_dsh_rejects_endseq_before_startseq():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "dsh",
        "sessionId": "sess-4",
        "turn": 7,
        "startSeq": 5,
        "endSeq": 3,
        "cwd": "/tmp/dsh",
        "sessionHeader": {},
        "events": [],
    }
    ok, _ = parse_writeback_command(body)
    assert ok is False


def test_skill_reminder_valid_and_trigger_validation():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "sess-5",
        "turnId": "t-5",
        "transcriptPath": "/tmp/sr.jsonl",
        "content": "Remember to run the linter before committing.",
        "triggers": ["cadence", "post_compaction"],
    }
    ok, command = parse_skill_reminder_command(body)
    assert ok is True
    assert command["triggers"] == ["cadence", "post_compaction"]


def test_skill_reminder_rejects_unknown_trigger():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "sess-5",
        "turnId": "t-5",
        "transcriptPath": "/tmp/sr.jsonl",
        "content": "x",
        "triggers": ["cadence", "evil_trigger"],
    }
    ok, _ = parse_skill_reminder_command(body)
    assert ok is False


def test_skill_reminder_rejects_unknown_key_fail_closed():
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "sess-5",
        "turnId": "t-5",
        "transcriptPath": "/tmp/sr.jsonl",
        "content": "x",
        "triggers": ["cadence"],
        "extra": 1,
    }
    ok, _ = parse_skill_reminder_command(body)
    assert ok is False


# ============ PII redaction ============


def test_redact_email():
    text = "contact me at zhang.chi@example.com for details"
    redacted, counts, flag = redact_text(text)
    assert flag is True
    assert counts.get("EMAIL", 0) == 1
    assert "zhang.chi@example.com" not in redacted
    assert "[REDACTED:EMAIL]" in redacted


def test_redact_cn_phone():
    text = "my mobile is 13812345678 call anytime"
    redacted, counts, _ = redact_text(text)
    assert "13812345678" not in redacted
    assert counts.get("PHONE", 0) == 1


def test_redact_cn_id_card_valid_checksum():
    # 11010519491231002X passes GB 11643-1999 checksum.
    text = "ID number: 11010519491231002X"
    redacted, counts, _ = redact_text(text)
    assert "11010519491231002X" not in redacted
    assert counts.get("ID_CARD", 0) == 1


def test_redact_cn_id_card_invalid_checksum_not_redacted():
    # Same number with a corrupted check digit must NOT be redacted.
    text = "ID number: 110105194912310021"
    redacted, _, _ = redact_text(text)
    assert "110105194912310021" in redacted


def test_redact_credit_card_luhn():
    text = "card 4242 4242 4242 4242 on file"
    redacted, counts, _ = redact_text(text)
    assert "4242" not in redacted
    assert counts.get("CREDIT_CARD", 0) == 1
    # Fails Luhn -> not a card -> untouched.
    text2 = "number 1234 5678 9012 3456 is an order id"
    redacted2, _, _ = redact_text(text2)
    assert "1234 5678 9012 3456" in redacted2


def test_redact_api_key_prefixes():
    text = "export OPENAI_API_KEY=sk-live-AbCdEf1234567890AbCdEf1234567890"
    redacted, counts, _ = redact_text(text)
    assert "sk-live-AbCdEf1234567890" not in redacted
    assert sum(counts.values()) >= 1


def test_redact_no_false_positive_plain_text():
    text = "The build produced 42 artifacts in 120 seconds across 3 nodes."
    redacted, counts, flag = redact_text(text)
    assert flag is False
    assert redacted == text
    assert counts == {}


def test_has_meaningful_text():
    assert has_meaningful_text("real content here") is True
    assert has_meaningful_text("...") is False
    assert has_meaningful_text("   ") is False


# ============ vectorizer ============


def test_char_ngram_vector_deterministic_and_normalized():
    v1 = char_ngram_vector("hello world")
    v2 = char_ngram_vector("hello world")
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-9
    assert len(v1) == 384


def test_cosine_similarity_bounds_and_zero():
    a = char_ngram_vector("deploy the gateway service")
    b = char_ngram_vector("deploy the gateway service")
    assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-9)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def test_vectorizer_backend_and_embedding_shape():
    vectorizer = DenseVectorizer()
    assert vectorizer.backend in ("gateway-embedding", "char-ngram")
    vec = vectorizer.embed("some text for embedding")
    assert len(vec) == 384
    assert vectorizer.embed("some text for embedding") == vec  # deterministic


# ============ store ============


def test_store_insert_idempotent_by_content_hash(workspace):
    store = get_memory_store()
    euid = effective_user_id(BASE_USER, "demo-repo")
    id1, created1 = store.insert_item(
        effective_user_id=euid, base_user_id=BASE_USER, repository_slug="demo-repo",
        memory_type=SEMANTIC, content="the sky is blue",
    )
    id2, created2 = store.insert_item(
        effective_user_id=euid, base_user_id=BASE_USER, repository_slug="demo-repo",
        memory_type=SEMANTIC, content="the sky is blue",
    )
    assert created1 is True and created2 is False
    assert id1 == id2
    # whitespace-normalized duplicates share the same hash
    assert content_hash(euid, "the sky is blue") == content_hash(euid, "the sky is blue  \n")


def test_store_list_filter_and_pagination(workspace):
    store = get_memory_store()
    euid = effective_user_id(BASE_USER, "demo-repo")
    for i in range(5):
        store.insert_item(
            effective_user_id=euid, base_user_id=BASE_USER, repository_slug="demo-repo",
            memory_type=CORE if i < 2 else SEMANTIC, content=f"memory number {i}",
        )
    all_items = store.list_items(euid, limit=10)
    assert len(all_items) == 5
    core_only = store.list_items(euid, memory_type=CORE, limit=10)
    assert len(core_only) == 2
    page = store.list_items(euid, limit=2, offset=4)
    assert len(page) == 1


def test_store_delete_is_scope_checked(workspace):
    store = get_memory_store()
    euid_a = effective_user_id(BASE_USER, "repo-a")
    euid_b = effective_user_id(BASE_USER, "repo-b")
    item_id, _ = store.insert_item(
        effective_user_id=euid_a, base_user_id=BASE_USER, repository_slug="repo-a",
        memory_type=CORE, content="scoped memory",
    )
    # deleting through the wrong scope must fail
    assert store.delete_item(item_id, euid_b) is False
    assert store.get_item(item_id) is not None
    assert store.delete_item(item_id, euid_a) is True
    assert store.get_item(item_id) is None


def test_store_turn_record_and_consume():
    store = get_memory_store()
    store.record_turn(client="codex", session_id="s", turn_id="t1", prompt="hello", cwd="/x")
    state = store.consume_turn(client="codex", session_id="s", turn_id="t1")
    assert state is not None and state["prompt"] == "hello"
    # consumed -> gone
    assert store.consume_turn(client="codex", session_id="s", turn_id="t1") is None


def test_store_dedupe_keys():
    store = get_memory_store()
    assert store.has_dedupe_key("k1") is False
    store.reserve_dedupe_keys(["k1", "k2"])
    assert store.has_dedupe_key("k1") is True
    assert store.has_dedupe_key("k2") is True


# ============ retrieval ============


def _seed(store, euid, contents, memory_type=SEMANTIC):
    slug = euid.split("@", 1)[1]
    base = euid.split("@", 1)[0]
    for content in contents:
        store.insert_item(
            effective_user_id=euid, base_user_id=base, repository_slug=slug,
            memory_type=memory_type, content=content,
        )


def test_recall_disabled_returns_skip(workspace):
    service = get_memory_service()
    result = hybrid_recall(
        service.store, service.vectorizer,
        query="anything", effective_user_id="u@r", cfg=_cfg(), retrieval_enabled=False,
    )
    assert result.retrieved is False
    assert result.skip_reason == "disabled"


def test_recall_skip_reasons_prompt_missing_and_control(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    r1 = hybrid_recall(service.store, service.vectorizer, query="   ",
                       effective_user_id=euid, cfg=_cfg(), retrieval_enabled=True)
    assert r1.skip_reason == "prompt_missing"
    r2 = hybrid_recall(service.store, service.vectorizer, query=":control",
                       effective_user_id=euid, cfg=_cfg(), retrieval_enabled=True)
    assert r2.skip_reason == "control_command"


def test_recall_hybrid_returns_xml_and_relevant_item(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    _seed(service.store, euid, [
        "Deployment procedure: run make release then push the docker image",
        "User likes concise answers in Chinese",
        "The weather in Hangzhou is rainy in spring",
    ])
    result = hybrid_recall(
        service.store, service.vectorizer,
        query="how do I deploy with make release?",
        effective_user_id=euid, cfg=_cfg(), retrieval_enabled=True,
    )
    assert result.retrieved is True
    assert result.context.startswith("<memories>")
    assert 'memory_type="' in result.context
    assert "make release" in result.context
    assert result.item_count >= 1
    # the deployment memory must outrank the unrelated weather memory
    assert "make release" in result.items[0]["content"]
    assert result.backend in ("gateway-embedding", "char-ngram")


def test_recall_respects_top_k(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    _seed(service.store, euid, [f"deployment note number {i} about make release" for i in range(10)])
    result = hybrid_recall(
        service.store, service.vectorizer,
        query="deployment make release", effective_user_id=euid,
        cfg=_cfg(top_k=3), retrieval_enabled=True,
    )
    assert result.retrieved is True
    assert result.item_count == 3


def test_recall_max_context_chars_truncation(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    _seed(service.store, euid, ["deploy " + "x" * 900])
    result = hybrid_recall(
        service.store, service.vectorizer,
        query="deploy", effective_user_id=euid,
        cfg=_cfg(max_context_chars=150, max_item_chars=200), retrieval_enabled=True,
    )
    assert result.retrieved is True
    assert len(result.context) <= 150 + 3  # truncation marker "..."
    assert result.context.endswith("...")


def test_recall_min_score_gate_filters_everything(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    _seed(service.store, euid, ["completely unrelated content zzz qqq"])
    result = hybrid_recall(
        service.store, service.vectorizer,
        query="kubernetes ingress routing tables",
        effective_user_id=euid, cfg=_cfg(min_score=0.99), retrieval_enabled=True,
    )
    assert result.retrieved is False
    assert result.skip_reason == "empty_context"


def test_recall_no_memories(workspace):
    service = get_memory_service()
    euid = _scope(workspace).effective_user_id
    result = hybrid_recall(service.store, service.vectorizer, query="anything",
                           effective_user_id=euid, cfg=_cfg(), retrieval_enabled=True)
    assert result.skip_reason == "no_memories"


def test_render_memories_xml_structure_and_escaping():
    rendered = [
        (CORE, 'prefers <tea> & "coffee"', 1700000000.0),
        (UNCLASSIFIED, "loose note", None),
    ]
    xml = render_memories_xml(rendered, [CORE, SEMANTIC], 1000, 4000)
    assert xml.startswith("<memories>")
    assert xml.rstrip().endswith("</memories>")
    assert f'memory_type="{CORE}"' in xml
    # unclassified not in the configured order -> appended bucket
    assert f'memory_type="{UNCLASSIFIED}"' in xml
    # XML escaping applied
    assert "&lt;tea&gt;" in xml
    assert "&amp;" in xml


def test_sparse_scores_rank_exact_match_first():
    query = tokenize("deploy gateway service")
    items = [
        tokenize("watermelons are green"),
        tokenize("deploy the gateway service now"),
        tokenize("deploy something else"),
    ]
    scores = _sparse_scores(query, items)
    assert scores[1] > scores[2] > scores[0]
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_recall_for_turn_returns_xml_or_none(workspace, memory_settings):
    service = get_memory_service()
    scope = _scope(workspace)
    _seed(service.store, scope.effective_user_id, ["deploy with make release"])
    context = service.recall_for_turn(
        "how to deploy with make release",
        base_user_id=BASE_USER, cwd=str(workspace),
    )
    assert context is not None and context.startswith("<memories>")
    # disabled path returns None and never raises
    memory_settings.memory.retrieval_enabled = False
    assert service.recall_for_turn("how to deploy", base_user_id=BASE_USER, cwd=str(workspace)) is None


# ============ writeback pipeline ============


def test_chunk_text_no_content_loss_and_overlap():
    text = "\n".join(f"line-{i}: {'x' * 60}" for i in range(300))
    chunks = chunk_text(text, 8000, 0.05)
    assert len(chunks) > 1
    assert all(len(c) <= 8000 for c in chunks)
    # overlap: tail of chunk n appears at the head of chunk n+1
    overlap = chunk_overlap_chars(8000, 0.05)
    assert overlap == 400
    tail = chunks[0][-overlap // 2:]
    assert tail in chunks[1][:overlap + overlap // 2] or chunks[1].startswith(chunks[0][-100:])
    # every input line survives chunking
    rejoined = chunks[0]
    for next_chunk in chunks[1:]:
        rejoined += next_chunk[overlap:] if next_chunk[:overlap] == rejoined[-overlap:] else next_chunk
    for i in range(300):
        assert f"line-{i}:" in rejoined


def test_chunk_text_empty_and_small():
    assert chunk_text("", 100, 0.05) == []
    assert chunk_text("   \n  ", 100, 0.05) == []
    assert chunk_text("tiny", 100, 0.05) == ["tiny"]


def test_build_transcript_and_group_prefix():
    transcript = build_transcript([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ])
    assert transcript == "user: q\nassistant: a"
    assert CHUNK_GROUP_PREFIX == "memory-writeback-chunk:v1:"


def _enqueue(store, vectorizer, cfg, scope, content, *, client="codex", session="s1", turn="t", now=1000.0):
    return enqueue_writeback(
        store, vectorizer, cfg,
        client=client, session_id=session, correlation_id=turn,
        scope=scope,
        messages=[{"role": "assistant", "content": content}],
        now=now,
    )


def test_enqueue_buffers_then_flushes_on_turn_count(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=2)
    r1 = _enqueue(store, vectorizer, cfg, scope, "first turn content", turn="t1")
    assert r1.accepted and r1.buffered and not r1.flushed
    assert store.count_items(scope.effective_user_id) == 0
    r2 = _enqueue(store, vectorizer, cfg, scope, "second turn content", turn="t2")
    assert r2.flushed is True
    assert r2.flush_reason == "turn_count"
    items = store.list_items(scope.effective_user_id, limit=10)
    assert len(items) == 1  # one chunk covering both turns
    assert items[0]["group_id"].startswith(CHUNK_GROUP_PREFIX)
    assert "first turn content" in items[0]["content"]
    assert "second turn content" in items[0]["content"]


def test_enqueue_flushes_on_char_limit(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=100, buffer_chars=200)
    r1 = _enqueue(store, vectorizer, cfg, scope, "a" * 300, turn="t1")
    assert r1.flushed is True and r1.flush_reason == "char_count"


def test_enqueue_flushes_on_age(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=100, buffer_seconds=10.0)
    r1 = _enqueue(store, vectorizer, cfg, scope, "early content", turn="t1", now=1000.0)
    assert not r1.flushed
    # a later turn 30s after creation trips the age limit
    r2 = _enqueue(store, vectorizer, cfg, scope, "late content", turn="t2", now=1030.0)
    assert r2.flushed is True and r2.flush_reason == "buffer_age"


def test_enqueue_duplicate_turn_is_idempotent(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=5)
    r1 = _enqueue(store, vectorizer, cfg, scope, "same turn twice", turn="dup")
    assert r1.accepted is True
    r2 = _enqueue(store, vectorizer, cfg, scope, "same turn twice", turn="dup")
    assert r2.accepted is False and r2.duplicate is True
    assert len(store.buffer_messages(r1.buffer_key)) == 1


def test_enqueue_redacts_pii_before_buffering(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=1, redact_pii=True)
    receipt = _enqueue(store, vectorizer, cfg, scope,
                       "email me at bob@example.com", turn="pii")
    assert receipt.flushed is True
    assert receipt.redacted_total >= 1
    assert receipt.redactions.get("EMAIL", 0) == 1
    items = store.list_items(scope.effective_user_id, limit=10)
    assert "bob@example.com" not in items[0]["content"]
    assert "[REDACTED:EMAIL]" in items[0]["content"]


def test_enqueue_redact_pii_toggle_off_keeps_content(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=1, redact_pii=False)
    _enqueue(store, vectorizer, cfg, scope, "email me at bob@example.com", turn="nor")
    items = store.list_items(scope.effective_user_id, limit=10)
    assert "bob@example.com" in items[0]["content"]


def test_enqueue_skips_non_meaningful_content(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    receipt = _enqueue(store, vectorizer, _cfg(), scope, "...", turn="dots")
    assert receipt.accepted is False
    assert receipt.skip_reason == "no_meaningful_content"
    # replay stays a no-op (key reserved)
    receipt2 = _enqueue(store, vectorizer, _cfg(), scope, "...", turn="dots")
    assert receipt2.duplicate is True


def test_flush_idempotent_replay_no_duplicates(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=1)
    r1 = _enqueue(store, vectorizer, cfg, scope, "repeatable content", turn="f1")
    assert r1.items_created == 1
    # manually re-flush the same content through a fresh buffer + turn key
    _enqueue(store, vectorizer, cfg, scope, "repeatable content", turn="f2")
    assert store.count_items(scope.effective_user_id) == 1  # content-hash dedupe


def test_flush_empty_buffer_reports_no_flush(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    report = flush_buffer(store, vectorizer, _cfg(), "ghost-buffer", reason="expired", now=1.0)
    assert report.flushed is False and report.reason == "empty_buffer"


def test_sweep_expired_buffers(workspace):
    store = get_memory_store()
    vectorizer = DenseVectorizer()
    scope = _scope(workspace)
    cfg = _cfg(buffer_turns=100, buffer_seconds=5.0)
    _enqueue(store, vectorizer, cfg, scope, "old buffered content", turn="old", now=100.0)
    assert store.count_items(scope.effective_user_id) == 0
    reports = sweep_expired_buffers(store, vectorizer, cfg, now=200.0)
    assert len(reports) == 1
    assert reports[0].flushed is True and reports[0].reason == "expired"
    assert store.count_items(scope.effective_user_id) == 1


def test_queue_writeback_full_cycle(workspace, memory_settings):
    memory_settings.memory.buffer_turns = 1
    service = get_memory_service()
    receipt = service.queue_writeback(
        base_user_id=BASE_USER,
        repository_slug="demo-repo",
        session_id="api-session",
        messages=[
            {"role": "user", "content": "what is the release process?"},
            {"role": "assistant", "content": "run make release to ship it"},
        ],
    )
    assert receipt["status"] == "ok"
    assert receipt["accepted"] is True
    assert receipt["flushed"] is True
    assert receipt["items_created"] >= 1
    assert service.count_items(base_user_id=BASE_USER, repository_slug="demo-repo") >= 1


def test_queue_writeback_disabled_by_default(workspace):
    # fresh default settings: writeback_enabled=False (iron rule)
    from moa_gateway.config import Settings as _S
    import moa_gateway.config as _cfgmod
    _cfgmod._settings = _S()
    service = get_memory_service()
    receipt = service.queue_writeback(
        base_user_id=BASE_USER, repository_slug="demo-repo", session_id="s",
        messages=[{"role": "assistant", "content": "x"}],
    )
    assert receipt["accepted"] is False
    assert receipt["reason"] == "writeback_disabled"


def test_default_settings_all_memory_flags_off():
    from moa_gateway.config import MemoryConfig

    cfg = MemoryConfig()
    assert cfg.retrieval_enabled is False
    assert cfg.writeback_enabled is False
    assert cfg.workspace_enabled is False
    assert cfg.top_k == 6
    assert cfg.max_context_chars == 4000
    assert cfg.max_item_chars == 1000
    assert cfg.buffer_turns == 8
    assert cfg.buffer_seconds == 600.0
    assert cfg.buffer_chars == 131_072
    assert cfg.chunk_chars == 8000
    assert cfg.chunk_overlap == pytest.approx(0.05)


def test_turn_idempotency_key_shape():
    assert turn_idempotency_key("codex", "s1", "t1") == "codex:s1:t1"


# ============ HTTP endpoints (M10) ============


def _codex_turn_start_http(cwd, prompt="how do I deploy with make release?"):
    return {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "http-sess",
        "turnId": "http-turn-1",
        "prompt": prompt,
        "cwd": str(cwd),
        "transcriptPath": "/tmp/http.jsonl",
    }


async def test_endpoint_requires_auth(client):
    resp = await client.post("/v1/memory/turn-start", json=_codex_turn_start_http("/tmp"))
    assert resp.status_code == 401


async def test_turn_start_endpoint_rejects_unknown_key_fail_closed(client):
    body = _codex_turn_start_http("/tmp")
    body["smuggled"] = True
    resp = await client.post("/v1/memory/turn-start", json=body, headers=AUTH)
    assert resp.status_code == 400


async def test_turn_start_endpoint_injects_recall_context(client, workspace):
    scope = resolve_memory_scope(workspace, BASE_USER)
    store = get_memory_store()
    store.insert_item(
        effective_user_id=scope.effective_user_id, base_user_id=BASE_USER,
        repository_slug=scope.repository_slug, memory_type=PROCEDURAL,
        content="Deployment procedure: run make release then push the docker image",
    )
    resp = await client.post(
        "/v1/memory/turn-start", json=_codex_turn_start_http(workspace), headers=AUTH
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["retrieved"] is True
    assert payload["context"].startswith("<memories>")
    assert "make release" in payload["context"]
    assert payload["scope"]["effective_user_id"] == scope.effective_user_id


async def test_turn_start_records_state_then_writeback_associates(client, workspace, memory_settings):
    memory_settings.memory.buffer_turns = 1
    turn_body = _codex_turn_start_http(workspace, prompt="what is the release process?")
    resp1 = await client.post("/v1/memory/turn-start", json=turn_body, headers=AUTH)
    assert resp1.status_code == 200
    writeback_body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "codex",
        "sessionId": "http-sess",
        "turnId": "http-turn-1",
        "lastAssistantMessage": "run make release to ship it",
        "cwd": str(workspace),
    }
    resp2 = await client.post("/v1/memory/writeback", json=writeback_body, headers=AUTH)
    assert resp2.status_code == 200
    payload = resp2.json()
    assert payload["accepted"] is True
    assert payload["turn_associated"] is True
    assert payload["flushed"] is True
    # stored chunk must pair the turn-start prompt with the assistant answer
    items_resp = await client.get(
        "/v1/memory/items", params={"repository": "demo-repo"}, headers=AUTH
    )
    items = items_resp.json()["items"]
    assert len(items) == 1
    assert "what is the release process?" in items[0]["content"]
    assert "run make release to ship it" in items[0]["content"]


async def test_writeback_endpoint_rejects_invalid_command(client):
    resp = await client.post(
        "/v1/memory/writeback",
        json={"version": 1, "client": "codex", "sessionId": "s", "bogus": 1},
        headers=AUTH,
    )
    assert resp.status_code == 400


async def test_skill_reminder_endpoint_records(client):
    body = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": "opencode",
        "sessionId": "sr-sess",
        "userMessageId": "u-42",
        "content": "Always run pytest before pushing.",
        "triggers": ["cadence"],
    }
    resp = await client.post("/v1/memory/skill-reminder", json=body, headers=AUTH)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["recorded"] is True
    assert payload["triggers"] == ["cadence"]
    reminders = get_memory_service().list_skill_reminders(session_id="sr-sess")
    assert len(reminders) == 1
    assert reminders[0]["content"] == "Always run pytest before pushing."


async def test_recall_endpoint_disabled_by_default(client, monkeypatch):
    import moa_gateway.config as _cfgmod
    _cfgmod._settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        }
    )
    resp = await client.get(
        "/v1/memory/recall", params={"query": "hello", "repository": "demo-repo"}, headers=AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["skip_reason"] == "disabled"


async def test_recall_endpoint_enabled_returns_context(client, workspace):
    scope = resolve_memory_scope(workspace, BASE_USER)
    store = get_memory_store()
    store.insert_item(
        effective_user_id=scope.effective_user_id, base_user_id=BASE_USER,
        repository_slug=scope.repository_slug, memory_type=SEMANTIC,
        content="FastAPI powers the gateway",
    )
    resp = await client.get(
        "/v1/memory/recall",
        params={"query": "what powers the gateway?", "repository": "demo-repo"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["retrieved"] is True
    assert "FastAPI" in payload["context"]
    assert payload["items"][0]["dense_score"] >= 0.0


async def test_items_endpoint_scoping(client, workspace):
    store = get_memory_store()
    store.insert_item(
        effective_user_id=effective_user_id(BASE_USER, "demo-repo"),
        base_user_id=BASE_USER, repository_slug="demo-repo",
        memory_type=CORE, content="visible item",
    )
    store.insert_item(
        effective_user_id=effective_user_id(BASE_USER, "other-repo"),
        base_user_id=BASE_USER, repository_slug="other-repo",
        memory_type=CORE, content="hidden item",
    )
    resp = await client.get(
        "/v1/memory/items", params={"repository": "demo-repo"}, headers=AUTH
    )
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["content"] == "visible item"
    assert "embedding" not in payload["items"][0]


async def test_delete_item_endpoint_scope_checked(client):
    store = get_memory_store()
    item_id, _ = store.insert_item(
        effective_user_id=effective_user_id(BASE_USER, "demo-repo"),
        base_user_id=BASE_USER, repository_slug="demo-repo",
        memory_type=CORE, content="doomed item",
    )
    # wrong scope -> 404
    resp = await client.delete(
        f"/v1/memory/items/{item_id}", params={"repository": "other-repo"}, headers=AUTH
    )
    assert resp.status_code == 404
    # right scope -> deleted
    resp2 = await client.delete(
        f"/v1/memory/items/{item_id}", params={"repository": "demo-repo"}, headers=AUTH
    )
    assert resp2.status_code == 200
    assert resp2.json()["deleted"] is True


async def test_capability_toggle_gates_memory_routes(client):
    from moa_gateway.capability_toggles import set_enabled

    set_enabled("memory", False)
    resp = await client.get(
        "/v1/memory/recall", params={"query": "x", "repository": "r"}, headers=AUTH
    )
    assert resp.status_code == 503
    set_enabled("memory", True)
    resp2 = await client.get(
        "/v1/memory/recall", params={"query": "x", "repository": "r"}, headers=AUTH
    )
    assert resp2.status_code == 200
