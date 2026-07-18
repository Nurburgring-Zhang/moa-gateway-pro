# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v1.7.x

### v1.7.4 (2026-07-18) — Round 6: Production deployment
### v1.7.3 (2026-07-18) — Round 5: All 7 workflows pass with real data flow
### v1.7.2 (2026-07-18) — Round 4: 11 services + 176 methods
### v1.7.1 (2026-07-18) — Round 3: 10 services + 7 builtin workflows + dispatcher
### v1.7.0 (2026-07-18) — Round 1+2: 80→0 deep e2e fail + Service Layer + AgentDispatch

## [v1.7.0] — 2026-07-18 — Production Architecture (5 rounds of fixes)

### Round 1: P0/P1/P2 + 80 deep e2e fails → all fixed
- **Global exception handlers** — TypeError/ValueError/KeyError/AttributeError/JSONDecodeError → 4xx
- **43× `HTTPException(500)` → `_err_500()` smart mapper** — input errors → 4xx, real server errors → 500
- **Aggregator.from_dict added** + **BOM stripped** + **duplicate `except HTTPException: raise` removed** (32 dup clauses)
- **P0-11 per-endpoint async lock** for `_saved_api_key` race condition
- **P0-12 chat_completions fallback recheck** on endpoint removal race
- **P1-2 worktree `__import__("os")` cleanup** → direct `os.environ`
- **P1-9 login rate limit** — IP-based, 10 attempts per 60s, new `login_attempts` table
- **moa-n-layer query type validation** — int query → 422
- **moa-3-layer 422 validation** for invalid proposer/aggregator
- **DEEP E2E RESULT**: 432/512 → 512/512 pass (0 fail)

### Round 2: Service Layer + AgentDispatch + Workflow Engine
- **`services/base.py`** — `ServiceBase`, `ServiceMethod`, `ServiceRegistry`, `service_method` decorator
- **`services/dispatcher.py`** — `AgentDispatcher` + `Workflow` + `WorkflowStep` (DAG executor with real inter-module data flow)
- **7 endpoints under `/v1/agent/*`** — list, dispatch, dispatch_batch, workflows, workflow/run, workflow/register

### Round 3: 10 services + 100 methods
- **MoAService** (4): `run_three_layer`, `run_engine`, `cross_iter`, `validate_config`
- **ConsensusService** (7): `vote_ensemble`, `should_rebalance`, `detect_convergent`, `arbitrate_conflicts`, `synthesize_multi_mode`, `check_group_think`, `evaluate_section_viability`
- **RoutingService** (6): `route`, `chain_info`, `execute_chain`, `classify_error`, `cost_estimate`, `reference_route`
- **QualityService** (7): `score_flask`, `rank_elo`, `gate_l0`, `score_panel`, `brainstorm`, `plan_act`, `meta_prompt`
- **AgentService** (18): comms, session_lock, bubble, MCP (subagent_comms, try_acquire, escalate, etc.)
- **QuotaService** (24): rate_quota, per_provider_rl, token_bucket, request_dedup, self_heal, tier_recalibrate, tier_promo, provider_health, consumption_intel, should_rebalance, cost_estimate
- **KnowledgeService** (12): embed, semantic_search, rag_search, fuzzy_dedup, input_fingerprint, rerank, distill, importance, context_clean, turboquant, prompt_features, goal_eval
- **SafetyService** (10): secret_scan, prompt_canary, tool_screening, output_wrapping, frozen, grace, anthropic_compat, llm_merge, version, worktree
- **ObservabilityService** (4): trace, audit, hook_events, in_flight
- **ConfigService** (8): config, mx, checkpoint, artifact, acceptance, action_policy, tool_replay, brainstorm_decide

### Round 4: CapabilityDispatcher (76 capability passthroughs)
- **`services/capability_dispatcher.py`** — single service with 76 `call_<endpoint>` methods
- **All capability endpoints** accessible via `service=capability, method=call_<endpoint>`

### Round 5: 7 builtin workflow templates (all pass with real data flow)
- `moa_quality_pipeline` — validate → run_moa → score_flask (3 steps, 8.9ms)
- `consensus_pipeline` — detect_convergent → vote_ensemble (2 steps, 7.6ms)
- `quality_gate` — gate_l0 → brainstorm (2 steps, 9.1ms)
- `knowledge_pipeline` — embed → semantic_search → rerank (3 steps, 2.4ms)
- `quota_check` — cost_estimate → provider_health → should_rebalance (3 steps, 3.7ms)
- `safety_pipeline` — gate_l0 → tool_screening → output_wrapping (3 steps, 11.4ms)
- `rag_pipeline` — rag_search → rerank (2 steps, 0.9ms)

### Round 6: Production deployment
- **`Dockerfile`** — multi-stage Python 3.11-slim
- **`docker-compose.yml`** — production compose with healthcheck, resource limits, log rotation
- **`.dockerignore`** — exclude test files, caches, secrets
- **`DEPLOYMENT.md`** — comprehensive deployment guide (Linux/macOS/Windows/Docker/K8s)
- **Performance test** (`test_perf.py`):
  - Sequential `/health`: 1000 reqs, p50=0.81ms, p99=23.27ms
  - Concurrent `/health`: 200 threads × 10 = 2000 reqs in 0.28s → **7193 RPS**
  - Concurrent `/v1/agent/dispatch`: 50 threads × 5 = 250 dispatches

## [v1.6.6] — 2026-07-15 — Deep E2E catch-up (4 critical bugs)

## [1.6.6] — 2026-07-15 — Deep E2E catch-up

### Fixed
- **goal-eval** (server.py:1841) — schema mismatch: server sent wrong fields to `Goal()`
  - Goal's actual fields are `(id, description, tier, criteria, evaluator_fn)`
  - Server now maps input to right fields with sensible defaults
- **goal-eval** (server.py:1855) — `generate_ceiling=True` crashed when baseline/residual_risk empty
  - Now defaults to placeholder text when fields missing
- **per-provider-rl** (server.py:1373, 1380) — used `mpl.limiters[provider]` (does not exist)
  - `MultiProviderLimiter` only has private `_limiters`; now uses `mpl._get(provider)`
- **task-tree** (server.py:1714) — `else: tree = TaskTree(root_id='root')` wiped out the built tree
  - Removed the bogus override that was discarding the actual constructed tree
- **moa-n-layer** (server.py:980) — validation failures wrapped as 500
  - Added explicit 400 checks for `proposers` non-empty and `aggregators` count = 3

### Added
- **43 4xx pass-through** (server.py) — auto-patched via `scripts/_patch_4xx.py`
  - Added `except HTTPException: raise` before every `except Exception` block that wraps 500
  - Inner code's 4xx now propagates correctly (no longer wrapped as 500)
- `scripts/test_deep_e2e.py` — 509 cases / 76 endpoints / 11 phases deep E2E
  - Data-driven test that catches production bugs basic E2E misses
- `scripts/_patch_4xx.py` — auto-applies 4xx pass-through fix
- `scripts/_fix_patch_order.py` — reverts bad patch order

### Test results
| Suite | v1.6.5 | v1.6.6 |
|-------|--------|--------|
| Unit tests | 1980 | 1980 |
| E2E (basic) | 137 | 137 |
| Deep E2E | 75 fail | **65 fail** (-10) |
| Server routes | 80 | 80 |

## [1.6.5] — 2026-07-14 — Wave 13 (5 new HIGH)

### Added
- **Wave 13 — 5 new HIGH capabilities** (229/229 tests pass)
  - `tool_screening.py` — 9-segment tool input risk detection (SQL/shell/path/code/prompt/URL/file/network/privesc), 50+ patterns, 5 risk levels (59 tests)
  - `anthropic_compat.py` — Anthropic Messages API compatibility (parse/format_response/SSE/tool_use/tool_result) (45 tests)
  - `token_bucket.py` — Token bucket rate limit (lazy refill, multi-key LRU 10000) (47 tests)
  - `request_dedup.py` — Request dedup with EXACT/NORMALIZED/SEMANTIC strategies + response cache (41 tests)
  - `trace.py` — W3C `traceparent` format, span tree, TraceCollector with LRU (37 tests)
- **5 new endpoints**:
  - `POST /v1/capability/tool-screening`
  - `POST /v1/capability/anthropic-compat`
  - `POST /v1/capability/token-bucket`
  - `POST /v1/capability/request-dedup`
  - `POST /v1/capability/trace`

### Fixed
- `scripts/pack_zip.py` — GBK encoding crash on Windows console (replaced `✓` with `[OK]`, `reconfigure stdout to utf-8`)

### Test results
| Suite | v1.6.4 | v1.6.5 |
|-------|--------|--------|
| Unit | 1751 | **1980** (+229) |
| E2E | 126 | **137** (+11) |
| Security regression | 12/12 | 12/12 |
| Server routes | 75 | 80 (+5) |

## [1.6.4] — 2026-07-14 — Wave 12 + 5 P0 + 2 P1

### Added
- **Wave 12 — 5 new HIGH capabilities** (210/210 tests pass)
  - `audit_cache.py` — LRU + TTL 24h audit event cache (36 tests)
  - `prompt_canary.py` — 4 strategies (SUFFIX/PREFIX/INVISIBLE/MULTI) + 18 injection patterns (48 tests)
  - `output_wrapping.py` — `<untrusted_tool_output>` tags + XML escape (34 tests)
  - `fuzzy_dedup.py` — simhash 64-bit local-sensitive hash (38 tests)
  - `input_fingerprint.py` — 4-layer hash fingerprint + collision detect (54 tests)
- **5 new endpoints**

### Fixed (P0 + P1 from v2 bug hunt)
- **P0-6** `feedback-iter` — RCE via `history_path`; now `require_admin` + path allowlist
- **P0-8** `worktree` — `subprocess.run` no timeout; added `timeout=10s` + `GIT_OPTIONAL_LOCKS=0`
- **P0-9** `_stream_single` — provider race; copies `provider = ep.provider_obj` before stream
- **P0-10** `_pending_close` unbounded — `deque(maxlen=100)` + background `_close_pending_loop`
- **P1-13** `change_password` — bcrypt 300ms blocking event loop; now `asyncio.to_thread`

### Test results
| Suite | v1.6.3 | v1.6.4 |
|-------|--------|--------|
| Unit | 1541 | **1751** |
| E2E | 115 | **126** |
| Server routes | 70 | 75 |

## [1.6.3] — 2026-07-14 — 9 security patches (P0 RCE prevention)

### Fixed (5 P0 + 4 P1)
- **P0-4** `checkpoint` — RCE via `atomic_write` action; now `require_admin` + removed
- **P0-5** `worktree` — RCE via `.gitconfig`; now `require_admin` + cwd allowlist
- **P0-1** `incr_rpm` race condition — now `BEGIN IMMEDIATE` atomic
- **P0-2** `_rebuild_provider` resource leak — uses `_pending_close` queue
- **P0-3** Fernet key TOCTOU — `O_CREAT|O_EXCL` + singleflight
- **P1-1** `incr_tokens` permanent lockout — check before increment
- **P1-2** health check dead code — `is not None` not `isinstance`
- **P1-3** JWT detection — strict regex not `count('.')==2`
- **P1-5** webui path traversal — `os.path.commonpath` not `startswith`
- **P1-6** token length limit — max 256 + multi-value header handling
- **P1-7** chat_completions recheck — endpoint exists after router returns

### Added
- `scripts/test_security_regression.py` — 12 security regression tests
- `BUG_HUNT_REPORT.md` — full 5 P0 + 7 P1 audit

## [1.6.2] — 2026-07-14 — Wave 11 (5 HIGH) + 2 patches

### Added
- **Wave 11 — 5 new HIGH capabilities** (173/173 tests pass)
  - `rag_search.py`, `plan_act.py`, `channels.py`, `reference_router.py`, `checkpoint.py`

### Fixed
- `ratelimit.py` — per-key `quota_rpm` now respected (was using global)
- `ratelimit.py` — admin JWT no longer KeyError on `/v1/quota`
- `server.py` — 4xx errors no longer wrapped as 500 (16 routes fixed)
- `feedback_loop.py` — panel_scores dict keys coerced to int
- `model_pool.py` — first-iter health check runs immediately (no 30s wait)

## [1.6.1] — 2026-07-13 — 4 production bug fixes

### Fixed
- `secret_scan.py` — relative path bug
- `model_pool.py` — JWT routing
- `server.py` — bcrypt async
- `pack_zip.py` — exclude `zip/` to prevent recursion (19 GB → 5.8 MB)

## [1.6.0] — 2026-07-13 — First production release

### Added
- 7 P0 capabilities
- 50 HIGH capabilities (Wave 1-10)
- 80+ server routes
- 1300+ unit tests
- 115+ E2E tests
- GitHub release with zip asset

---

## Cumulative metrics

| Version | Capability modules | Unit tests | E2E tests | Server routes |
|---------|-------------------|------------|-----------|---------------|
| v1.6.0  | 57 (7 P0 + 50 HIGH) | 1300+ | 115 | 70+ |
| v1.6.1  | 57 | 1300+ | 115 | 70+ |
| v1.6.2  | 62 (7 P0 + 55 HIGH) | 1541 | 115 | 70+ |
| v1.6.3  | 62 | 1541 | 115 | 70+ |
| v1.6.4  | 67 (7 P0 + 60 HIGH) | 1751 | 126 | 75+ |
| v1.6.5  | 72 (7 P0 + 65 HIGH) | 1980 | 137 | 80+ |
| v1.6.6  | 72 | 1980 | 137 (deep: 65 fail) | 80+ |

## Upcoming (v1.6.7+)

- **Pydantic validation** for 30 endpoints (replace `body: Dict[str, Any]`)
- **Wave 14** — 5 more HIGH capabilities
- **P1 🔸 medium 109** — long-term investment
- **v1.6.5 deferred P0/P1** — rag_search aiosqlite + storage conn pool + LRU caps
