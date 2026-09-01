"""moa_gateway.memory — MemoraX-Code-style cross-session memory layer (M10).

Ported from MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT).

Modules:
- ``scope``         : memory scope model (``effective_user_id = base@repo``)
- ``classifier``    : 5-type memory classifier (core/episodic/semantic/
                      procedural/unclassified)
- ``hook_protocol`` : fail-closed hook command parsing (key whitelists)
- ``redaction``     : PII redaction (regex + Luhn/GB11643 validators)
- ``vectorizer``    : dense channel (gateway embedding -> n-gram fallback)
- ``store``         : SQLite persistence (idempotent, content-hash dedupe)
- ``retrieval``     : hybrid dense+sparse recall + ``<memories>`` XML render
- ``writeback``     : buffer -> chunk -> idempotent store pipeline
- ``service``       : orchestration + clean assistant integration API

Both retrieval and writeback are OFF by default (``MemoryConfig``):
this layer never mutates conversational traffic unless explicitly enabled.
"""

from .classifier import (
    CORE,
    EPISODIC,
    MEMORY_TYPES,
    PROCEDURAL,
    SEMANTIC,
    UNCLASSIFIED,
    classify_memory_type,
    normalize_memory_type,
)
from .hook_protocol import (
    MEMORY_HOOK_CLIENTS,
    MEMORY_HOOK_COMMAND_VERSION,
    parse_skill_reminder_command,
    parse_turn_start_command,
    parse_writeback_command,
    turn_correlation_id,
)
from .redaction import has_meaningful_text, redact_text
from .retrieval import RecallResult, hybrid_recall, render_memories_xml, tokenize
from .scope import MemoryScope, effective_user_id, resolve_memory_scope
from .service import MemoryService, get_memory_service, reset_memory_service
from .store import MemoryStore, get_memory_store, reset_memory_store
from .vectorizer import DenseVectorizer, char_ngram_vector, cosine_similarity
from .writeback import (
    CHUNK_GROUP_PREFIX,
    FlushReport,
    WritebackReceipt,
    build_transcript,
    chunk_text,
    enqueue_writeback,
    extract_turn_messages,
    flush_buffer,
    turn_idempotency_key,
)

__all__ = [
    "CHUNK_GROUP_PREFIX",
    "CORE",
    "EPISODIC",
    "MEMORY_HOOK_CLIENTS",
    "MEMORY_HOOK_COMMAND_VERSION",
    "MEMORY_TYPES",
    "PROCEDURAL",
    "SEMANTIC",
    "UNCLASSIFIED",
    "MemoryScope",
    "MemoryStore",
    "MemoryService",
    "DenseVectorizer",
    "FlushReport",
    "RecallResult",
    "WritebackReceipt",
    "build_transcript",
    "char_ngram_vector",
    "chunk_text",
    "classify_memory_type",
    "cosine_similarity",
    "effective_user_id",
    "enqueue_writeback",
    "extract_turn_messages",
    "flush_buffer",
    "get_memory_service",
    "get_memory_store",
    "has_meaningful_text",
    "hybrid_recall",
    "normalize_memory_type",
    "parse_skill_reminder_command",
    "parse_turn_start_command",
    "parse_writeback_command",
    "redact_text",
    "render_memories_xml",
    "reset_memory_service",
    "reset_memory_store",
    "resolve_memory_scope",
    "tokenize",
    "turn_correlation_id",
    "turn_idempotency_key",
]
