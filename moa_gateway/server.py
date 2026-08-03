"""moa_gateway.server — FastAPI application entry point.

Provides:
- App creation with lifespan management
- Middleware configuration (CORS, security headers, body size limit)
- Global exception handlers
- Router registration from route modules
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .audit import setup_audit_logging
from .cache.manager import get_cache_manager
from .config import get_settings
from .model_pool import get_model_pool
from .health import init_health_system, shutdown_health_system
from .benchmark import init_benchmark_system, shutdown_benchmark_system
from .discovery import DiscoveryScheduler, FreeModelDiscoveryEngine, AutoConfigurator
from .observability import Metrics, ObservabilityMiddleware, setup_logging
from .storage import get_storage
from .ha import graceful, health_checker

logger = logging.getLogger(__name__)


# ========== Auto-Bootstrap Helpers ==========

def _load_dotenv() -> None:
    """加载项目根目录的.env文件到环境变量（仅设置未被系统覆盖的）"""
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            # 去除引号包裹
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to load .env: %s", e)


def _ensure_gateway_key(settings) -> None:
    """确保至少有一个gateway API key"""
    if settings.auth.gateway_api_keys:
        return
    # 从环境变量读取
    env_key = os.environ.get("MOA_GATEWAY_KEY", "").strip()
    if env_key:
        settings.auth.gateway_api_keys = [env_key]
        logger.info("gateway_api_keys loaded from MOA_GATEWAY_KEY env var")
    else:
        # 自动生成一个临时key
        auto_key = f"moa-auto-{secrets.token_urlsafe(24)}"
        settings.auth.gateway_api_keys = [auto_key]
        logger.warning(
            "\n" + "=" * 60 + "\n"
            "  \u26a0 未配置 gateway_api_keys，已自动生成临时Key:\n"
            f"  {auto_key}\n"
            "  请将此Key用于API调用的 Authorization: Bearer <key>\n"
            "  建议在 .env 文件中设置 MOA_GATEWAY_KEY 以持久化\n"
            + "=" * 60
        )


def _ensure_admin_password(settings) -> None:
    """确保admin密码已设置"""
    if settings.auth.admin_password:
        return
    env_pwd = os.environ.get("MOA_ADMIN_PASSWORD", "").strip()
    if env_pwd:
        settings.auth.admin_password = env_pwd
        logger.info("admin_password loaded from MOA_ADMIN_PASSWORD env var")
    else:
        auto_pwd = secrets.token_urlsafe(12)
        settings.auth.admin_password = auto_pwd
        logger.warning(
            "  \u26a0 未配置admin密码，已自动生成: %s", auto_pwd
        )


def _print_startup_summary(settings) -> None:
    """打印启动配置摘要"""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  [*] MOA-Gateway-Pro 启动完成")
    lines.append("=" * 60)

    # Gateway Key
    keys = settings.auth.gateway_api_keys
    if keys:
        display_key = keys[0] if len(keys[0]) <= 24 else keys[0][:20] + "..."
        lines.append(f"  \u2713 Gateway API Key: {display_key}")
    else:
        lines.append("  \u2717 Gateway API Key: 未配置（所有请求将被拒绝）")

    # 检查有哪些真实Provider
    real_providers = []
    mock_providers = []
    for model in settings.models:
        key = model.api_key or os.environ.get(model.api_key_env or "", "")
        if key:
            real_providers.append(model.id)
        else:
            mock_providers.append(model.id)

    if real_providers:
        display = ", ".join(real_providers[:5])
        if len(real_providers) > 5:
            display += "..."
        lines.append(f"  \u2713 真实模型: {len(real_providers)}个 ({display})")
    else:
        lines.append("  \u26a0 真实模型: 0个（全部使用MockProvider）")
        lines.append("    \u2192 请在 .env 中配置至少一个LLM Key")
        lines.append("    \u2192 推荐: GROQ_API_KEY (免费) https://console.groq.com/keys")

    if mock_providers:
        lines.append(f"  \u25cb Mock模型: {len(mock_providers)}个")

    # 多模态状态
    multimodal_keys = {
        "ELEVENLABS_API_KEY": "语音(TTS/ASR)",
        "TAVILY_API_KEY": "Web搜索",
    }
    for env_key, desc in multimodal_keys.items():
        if os.environ.get(env_key):
            lines.append(f"  \u2713 {desc}: 已配置")

    port = settings.server.port
    lines.append(f"\n  [i] API文档: http://localhost:{port}/docs")
    lines.append("=" * 60)
    lines.append("")

    print("\n".join(lines))


async def _daily_purge_loop(purge_manager) -> None:
    """Background loop: daily purge check for dead endpoints."""
    import time as _time
    last_purge = 0.0
    while True:
        try:
            now = _time.time()
            if now - last_purge > 86400:
                purged = await purge_manager.check_and_purge()
                if purged:
                    logger.info('Daily purge: removed %d dead endpoints', len(purged))
                last_purge = now
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning('Daily purge loop error: %s', e)
            await asyncio.sleep(300)


# ========== FastAPI App ==========
def create_app() -> FastAPI:
    # Auto-Bootstrap: 加载.env文件（在任何配置加载之前）
    _load_dotenv()

    settings = get_settings()

    # Auto-Bootstrap: 确保关键配置存在
    _ensure_gateway_key(settings)
    _ensure_admin_password(settings)

    setup_logging(
        settings.server.log_level, settings.observability.log_dir, settings.observability.log_json
    )
    get_storage()  # init singleton
    pool = get_model_pool()
    Metrics.instance()  # init singleton

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("MoA Gateway Pro starting up…")

        # SEC-002: Security config check on startup
        _security_warnings = []
        _sec_settings = get_settings()
        if not _sec_settings.auth.jwt_secret:
            _security_warnings.append("jwt_secret is empty — JWT tokens will be insecure")
        elif len(_sec_settings.auth.jwt_secret) < 32:
            _security_warnings.append(
                f"jwt_secret is too short ({len(_sec_settings.auth.jwt_secret)} chars, minimum 32). "
                "Set a strong secret via MOA_JWT_SECRET environment variable."
            )
        _weak_pws = {"admin", "123456", "password", "12345678", "qwerty", "abc123", "root", ""}
        if _sec_settings.auth.admin_password in _weak_pws:
            _security_warnings.append(
                f"admin_password is weak ('{_sec_settings.auth.admin_password}') \u2014 "
                "set a strong password via MOA_ADMIN_PASSWORD env or config.yaml"
            )
        if "demo-key-please-change" in _sec_settings.auth.gateway_api_keys:
            _security_warnings.append(
                "gateway_api_keys contains 'demo-key-please-change' \u2014 "
                "remove it and generate a real API key"
            )
        _is_production = os.environ.get("MOA_ENV", "").lower() in ("production", "prod")
        for w in _security_warnings:
            logger.warning("[SECURITY] %s", w)
        _critical_failures = [w for w in _security_warnings if "jwt_secret is empty" in w]
        if _critical_failures:
            raise RuntimeError(
                f"FATAL: Critical security configuration error (applies to ALL modes): "
                f"{'; '.join(_critical_failures)}"
            )
        if _is_production and _security_warnings:
            raise RuntimeError(
                "Refusing to start in production mode with insecure configuration. "
                f"Issues: {'; '.join(_security_warnings)}"
            )

        await pool.start()
        logger.info("Model pool started: %d endpoints", len(pool.endpoints))

        # Task #43: Initialize API health management system
        api_health_checker, probe_engine, purge_manager = init_health_system(
            model_pool=pool,
            storage=get_storage(),
        )
        logger.info("Health management system initialized")

        # Start monitoring all existing endpoints
        endpoint_ids = list(pool.endpoints.keys())
        if settings.health.enabled and endpoint_ids:
            await probe_engine.start_all(endpoint_ids)
            logger.info("Started health monitoring for %d endpoints", len(endpoint_ids))

        # Task #44: Initialize benchmark and capability system (P2-5: respect enabled flag)
        bench_engine = None
        cap_probe = None
        if settings.benchmark.enabled:
            bench_engine, cap_probe = init_benchmark_system(
                model_pool=pool,
                health_checker=api_health_checker,
            )
            await bench_engine.start()
            await cap_probe.start()
            # P2-6: Wire benchmark/capability cleanup into PurgeManager
            purge_manager.set_cleanup_targets(
                benchmark_engine=bench_engine,
                capability_probe=cap_probe,
            )
            logger.info("Benchmark system initialized (engine + capability probe)")
        else:
            logger.info("Benchmark system disabled by config")

        # Task #45: Initialize MoaOptimizer (P2-5: respect enabled flag)
        optimizer = None
        if settings.optimizer.enabled:
            from .moa_optimizer import MoaOptimizer
            optimizer = MoaOptimizer(
                benchmark_engine=bench_engine,
                capability_probe=cap_probe,
                health_checker=api_health_checker,
                model_pool=pool,
            )
            from .routes import optimizer as _opt_mod
            _opt_mod._optimizer_singleton = optimizer
            logger.info("MoaOptimizer initialized")
        else:
            logger.info("MoaOptimizer disabled by config")

        # P1-1: Instantiate and start DiscoveryScheduler
        discovery_scheduler = None
        if settings.discovery.enabled:
            discovery_engine = FreeModelDiscoveryEngine()
            configurator = AutoConfigurator(pool=pool, storage=get_storage())
            discovery_scheduler = DiscoveryScheduler(
                engine=discovery_engine,
                configurator=configurator,
                probe_engine=probe_engine,
                purge_manager=purge_manager,
                benchmark_engine=bench_engine,
                capability_probe=cap_probe,
                optimizer=optimizer,
            )
            await discovery_scheduler.start(
                interval_hours=settings.discovery.refresh_interval_hours
            )
            logger.info(
                "DiscoveryScheduler started (interval=%dh)",
                settings.discovery.refresh_interval_hours,
            )
        else:
            logger.info("Discovery system disabled by config")

        # Start daily purge check task
        purge_task = asyncio.create_task(_daily_purge_loop(purge_manager))

        cleanup_task = asyncio.create_task(_background_cleanup_loop())

        # Initialize cache system
        await get_cache_manager().initialize()

        # Initialize test report generator (P1-6)
        from .observability.test_report import init_report_generator
        report_storage = os.path.join("data", "reports")
        init_report_generator(storage_dir=report_storage)
        logger.info("Test report generator initialized (storage=%s)", report_storage)

        # HA: Mark instance as ready to receive traffic
        health_checker.mark_ready()
        logger.info("Instance marked READY \u2014 accepting traffic")

        # Auto-Bootstrap: 打印启动配置摘要
        _print_startup_summary(settings)

        yield
        # HA: Mark not ready during shutdown (drain from LB)
        health_checker.mark_not_ready()
        # Shutdown cache
        await get_cache_manager().shutdown()

        cleanup_task.cancel()
        try:
            await cleanup_task
        except (asyncio.CancelledError, Exception):
            pass

        logger.info("MoA Gateway Pro shutting down…")
        # Task #43: Shutdown health management system
        await shutdown_health_system()
        # P1-1: Stop DiscoveryScheduler
        if discovery_scheduler is not None:
            await discovery_scheduler.stop()
        # Task #44: Shutdown benchmark system
        await shutdown_benchmark_system()
        try:
            purge_task.cancel()
            await purge_task
        except (asyncio.CancelledError, Exception):
            pass
        # HA: Wait for active requests to drain
        await graceful.shutdown()
        await pool.stop()

    async def _background_cleanup_loop():
        """Background loop: clean old logs and rate-limit buckets."""
        from .storage import get_storage

        storage = get_storage()
        settings = get_settings()
        last_log_cleanup = 0
        last_rl_cleanup = 0
        while True:
            try:
                now = time.time()
                if now - last_log_cleanup > 86400:
                    deleted = storage.cleanup_old_logs(settings.storage.log_retention_days)
                    if deleted:
                        logger.info("cleanup_old_logs: removed %d rows", deleted)
                    last_log_cleanup = now
                if now - last_rl_cleanup > 3600:
                    cutoff = now - 7200
                    with storage.conn() as c:
                        cur = c.execute(
                            "DELETE FROM ratelimit_buckets WHERE updated_at < ?", (cutoff,)
                        )
                        if cur.rowcount:
                            logger.info("cleanup ratelimit buckets: removed %d", cur.rowcount)
                    last_rl_cleanup = now
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("background cleanup error: %s", e)
                await asyncio.sleep(300)

    app = FastAPI(
        title="MoA Gateway Pro",
        version="1.6.6",
        description="工业级多模型协作网关 — 一份 OpenAI Key 接入所有大模型",
        lifespan=lifespan,
    )

    # ============ Global Exception Handlers ============
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request, exc: RequestValidationError):
        try:
            detail = exc.errors()
        except Exception:
            detail = str(exc)
        return JSONResponse(
            status_code=422,
            content={"detail": "validation error", "errors": detail},
        )

    @app.exception_handler(ValueError)
    async def _value_handler(request, exc: ValueError):
        msg = str(exc) or exc.__class__.__name__
        return JSONResponse(
            status_code=400,
            content={"detail": f"value error: {msg}"},
        )

    @app.exception_handler(TypeError)
    async def _type_handler(request, exc: TypeError):
        msg = str(exc) or exc.__class__.__name__
        return JSONResponse(
            status_code=422,
            content={"detail": f"type error: {msg}"},
        )

    @app.exception_handler(KeyError)
    async def _key_handler(request, exc: KeyError):
        return JSONResponse(
            status_code=422,
            content={"detail": f"missing required field: {exc.args[0] if exc.args else 'unknown'}"},
        )

    @app.exception_handler(AttributeError)
    async def _attr_handler(request, exc: AttributeError):
        msg = str(exc) or exc.__class__.__name__
        return JSONResponse(
            status_code=422,
            content={"detail": f"attribute error: {msg}"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(json.JSONDecodeError)
    async def _json_handler(request, exc: json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content={"detail": f"invalid JSON: {exc.msg}"},
        )

    @app.exception_handler(IndexError)
    async def _index_handler(request, exc: IndexError):
        return JSONResponse(
            status_code=422,
            content={"detail": f"index out of range: {exc}"},
        )

    @app.exception_handler(ZeroDivisionError)
    async def _zero_handler(request, exc: ZeroDivisionError):
        return JSONResponse(
            status_code=422,
            content={"detail": f"division by zero: {exc}"},
        )

    # ============ Middleware ============
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ============ Observability Middleware (trace injection + metrics) ============
    app.add_middleware(ObservabilityMiddleware)

    # Multimodal upload paths allowed larger body size
    LARGE_BODY_PATHS = (
        "/v1/images/edits",
        "/v1/images/variations",
        "/v1/audio/",
        "/v1/video/",
    )

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        # Allow larger body for multimodal upload endpoints
        if any(request.url.path.startswith(p) for p in LARGE_BODY_PATHS):
            max_body = 25 * 1024 * 1024  # 25MB for multimodal
        else:
            max_body = 1 * 1024 * 1024  # 1MB for regular API calls
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > max_body:
            return JSONResponse(
                {"detail": f"request body too large (> {max_body} bytes)"},
                status_code=413,
            )
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self';"
        )
        return resp

    # ============ Audit Logging ============
    setup_audit_logging()

    # ============ Register Route Modules ============
    from .routes import (
        admin_router,
        agent_router,
        auth_router,
        capability_router,
        chat_router,
        health_router,
        mcp_router,
        metrics_router,
        moa_router,
        models_router,
        webui_router,
        compliance_router,
        workflow_router,
        observability_router,
        benchmark_router,
        optimizer_router,
        vision_router,
        audio_router,
        image_edit_router,
        video_router,
        embodied_router,
        world_model_router,
        threed_router,
        assistant_router,
    )

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(mcp_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(moa_router)
    app.include_router(capability_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(agent_router)
    app.include_router(webui_router)
    app.include_router(compliance_router)
    app.include_router(workflow_router)
    app.include_router(observability_router)
    app.include_router(benchmark_router)
    app.include_router(optimizer_router)
    app.include_router(vision_router)
    app.include_router(audio_router)
    app.include_router(image_edit_router)
    app.include_router(video_router)
    app.include_router(embodied_router)
    app.include_router(world_model_router)
    app.include_router(threed_router)
    app.include_router(assistant_router)

    return app


# ========== Entry Point ==========
app = create_app()

# Re-export for backward compatibility (tests and external code import from here)
from .routes.chat import ChatCompletionRequest, ChatMessage  # noqa: E402,F401

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "moa_gateway.server:app",
        host=s.server.host,
        port=s.server.port,
        workers=s.server.workers,
        log_level=s.server.log_level.lower(),
    )
