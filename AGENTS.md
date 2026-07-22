# AGENTS.md

Engineering guide for AI agents working on this codebase.

## Project

**MoA Gateway Pro** — Industrial-grade multi-model collaboration gateway.
Single OpenAI-compatible API that fans out to multiple LLM providers with
MoA (Mixture of Agents) aggregation.

**Stack**: Python 3.10+ / FastAPI / Pydantic v2 / SQLite / httpx

## Critical rules

1. **No stubs / mocks in production code** — every function ships working logic.
   Tests may mock; `MockProvider` is OK for fallback when API keys are missing.

2. **No silent `except Exception: pass`** — always log + raise or propagate.

3. **HTTPException must propagate** — every `except Exception` block that
   wraps `raise HTTPException(500, ...)` MUST have `except HTTPException: raise`
   before it, so 4xx errors aren't wrapped as 500.

4. **Per-key rate limit must respect `key_info['quota_rpm']`** — do not use
   global `per_key_rpm` for individual API keys.

5. **Atomic DB ops** — `incr_rpm` / `incr_daily_tokens` use `BEGIN IMMEDIATE`
   inside the connection context, never separate read+write.

6. **Path traversal defense** — endpoints that accept `path` parameters
   MUST use `os.path.commonpath` validation, not `startswith`.

7. **WAL mode for SQLite** — high-concurrency access requires
   `PRAGMA journal_mode=WAL`.

8. **Capability endpoint permission model**:
   - Default: `require_api_key` (any valid key)
   - Sensitive (file write, git cwd, admin actions): `require_admin`
   - Never expose RCE-capable primitives to API-key users

## Module layout

```
moa_gateway/
  server.py           — FastAPI app, all routes
  config.py           — Pydantic settings
  storage.py          — SQLite ORM + Fernet crypto
  auth.py             — API key + JWT auth
  ratelimit.py        — per-key rate limiting
  model_pool.py       — LLM provider pool + health check loop
  providers/          — provider implementations (OpenAI compat, Anthropic, etc.)
  capability/         — 70+ capability modules (one per feature)
    tests/            — pytest tests, one file per module
```

## Adding a new capability module

1. Create `moa_gateway/capability/<name>.py` with the implementation
2. Create `moa_gateway/capability/tests/test_<name>.py` with ≥20 test cases
3. Verify it imports: `python -c "from moa_gateway.capability import <name>"`
4. Add server endpoint in `moa_gateway/server.py` (follow existing pattern:
   `@app.post("/v1/capability/<name>")` with proper auth)
5. Add to `scripts/test_full_e2e.py` capabilities list
6. Add to `scripts/test_deep_e2e.py` (if not auto-detected)
7. Update `CHANGELOG.md`

## Testing discipline

- **Unit tests**: one file per module, ≥20 cases, edge cases + concurrency
- **Basic E2E**: 11 phases, ~140 cases, runs in ~30s
- **Deep E2E**: 76 endpoints × all actions × type/empty/missing variants, ~509 cases
- **Security regression**: 12 cases (auth bypass, path traversal, JWT, etc.)
- **No skipping**: tests must run end-to-end. Use `try/except` per case, never abort early.

## Versioning

- **MAJOR** (1.x → 2.x): breaking API changes
- **MINOR** (1.6.x → 1.7.x): new features, deprecations allowed
- **PATCH** (1.6.5 → 1.6.6): bug fixes only
- Every release: CHANGELOG.md update + GitHub release + zip asset upload

## What NOT to do

- Don't use `eval()` or `exec()` for dynamic dispatch — use enums + dict
- Don't use `*args, **kwargs` for Pydantic request bodies — define models
- Don't return `Dict[str, Any]` from public APIs — use TypedDict or Pydantic
- Don't use `os.system()` or `shell=True` — use `subprocess.run(..., check=True)`
- Don't `print()` for logging — use `logger` from `logging` module
- Don't skip tests to make CI green — fix the underlying issue


## Communication style

- 说人话,严禁堆术语
- 短句,数字+动词,不要形容词
- 不写文档式输出(去掉首先/其次/最后)
- 不用客服腔(您/请/感谢)
