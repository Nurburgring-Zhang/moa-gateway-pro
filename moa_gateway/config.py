"""moa_gateway.config — 配置管理
负责加载 config.yaml + 数据库里的覆盖配置 + 环境变量,
对全系统暴露统一的 Settings 对象。
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 默认配置目录
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.yaml"
DATA_DIR = ROOT_DIR / "data"


class ModelEndpointConfig(BaseModel):
    """模型端点配置"""

    id: str
    provider: str
    model: str
    tier: Literal["free", "lite", "standard", "premium", "flagship"] = "standard"
    api_base: str = ""
    api_key_env: str = ""  # 优先从环境变量取
    api_key: str = ""  # 也可直接在配置里写(不推荐)
    cost_per_1k_input: float = 0.001
    cost_per_1k_output: float = 0.002
    max_tokens: int = 8192
    timeout: int = 120
    weight: int = 100
    enabled: bool = False
    tags: list[str] = Field(default_factory=list)
    # 运行时字段(不存到 yaml)
    api_key_runtime: str = ""  # 启动时从 env 或 webui 注入的 key
    health_status: str = "unknown"
    consecutive_failures: int = 0
    last_health_check: float = 0.0


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8910
    workers: int = 1
    log_level: str = "INFO"
    # P1-2 安全加固:默认 CORS 改为精确 origin,不再用 "*"
    # 用户如需跨域访问,显式在 config.yaml 里添加可信 origin
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8910", "http://127.0.0.1:8910"]
    )


class StorageConfig(BaseModel):
    db_path: str = "data/config.db"
    log_retention_days: int = 30
    # Database URL - supports SQLite (default) and PostgreSQL
    # SQLite:      sqlite:///./data/config.db  (or leave empty for db_path)
    # PostgreSQL:  postgresql+psycopg2://user:pass@localhost:5432/moa_gateway
    database_url: str = ""
    # Connection pool settings (PostgreSQL only)
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600


class AuthConfig(BaseModel):
    # P1-4 安全加固:默认 gateway_api_keys 为空(必须显式添加或通过 WebUI 生成)
    gateway_api_keys: list[str] = Field(default_factory=list)
    admin_username: str = "admin"
    admin_password: str = ""
    jwt_secret: str = ""
    jwt_expire_minutes: int = 1440


class RoutingConfig(BaseModel):
    thresholds: dict[str, int] = Field(
        default_factory=lambda: {
            "trivial_length": 10,
            "simple_length": 50,
            "medium_length": 200,
            "complex_length": 500,
        }
    )
    tier_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "trivial": "free",
            "simple": "lite",
            "medium": "standard",
            "complex": "premium",
            "expert": "flagship",
        }
    )
    max_cost_per_request: float = 1.0
    max_retries: int = 3


class MoAStageConfig(BaseModel):
    name: str
    tier: str = "standard"


class ReferenceModelConfig(BaseModel):
    """显式指定的参考模型 — 用于国家队 preset 等场景
    参考 Hermes v0.18 MoA reference_models 列表配置方式。
    """

    id: str = ""  # 留空表示动态选
    provider: str = ""  # 可选,选模型时偏好
    model: str = ""  # 可选,具体的模型名
    role: str = ""  # compose 模式下的角色(aspect),如 "feasibility" / "performance"
    weight: int = 100  # 多个候选时的权重
    required: bool = False  # 是否必须用这个模型(找不到就报错)


class MoAPresetConfig(BaseModel):
    enabled: bool = True
    strategy: Literal[
        "single",
        "parallel",
        "pipeline",
        "compose",
        "judge",
        "chain",
        "layered",
        "single_proposer",
        "ranker",
    ] = "parallel"
    # 显式参考模型列表(Hermes v0.18 风格)— 留空表示动态选择
    reference_models: list[ReferenceModelConfig] = Field(default_factory=list)
    reference_count: int = 3  # 动态选时的目标数量
    aggregator: str = ""  # 显式指定 aggregator model id
    aggregator_tier: str = "premium"  # 动态选 aggregator 时的 tier
    tier: str = "standard"  # 动态选参考模型时的 tier
    critic_rounds: int = 1
    # 修:参考模型和聚合器独立温度 — 借鉴 Hermes v0.18
    reference_temperature: float = 0.6  # 参考模型稍高(多样性)
    aggregator_temperature: float = 0.4  # 聚合器稍低(稳定/裁决)
    max_tokens: int = 4096
    stages: list[MoAStageConfig] = Field(default_factory=list)
    # Layered MoA 层数(论文 §2.2)
    layer_count: int = 3
    # 描述(给人看)
    description: str = ""


class MoAConfig(BaseModel):
    enabled: bool = True
    default_preset: str = "balanced"
    reference_models: int = 4
    aggregator: str = ""
    critic_rounds: int = 1
    reference_timeout: int = 60
    aggregator_timeout: int = 120
    consensus_threshold: float = 0.35
    presets: dict[str, MoAPresetConfig] = Field(default_factory=dict)


class HealthConfig(BaseModel):
    """Health check and API health management configuration."""

    # Legacy health check settings (used by ModelPool._health_check_loop)
    interval_seconds: int = 30
    timeout_seconds: int = 10
    failure_threshold: int = 3
    cooldown_seconds: int = 60
    healthy_recheck: int = 120
    # API health management system (Task #43)
    enabled: bool = True
    probe_interval_new: int = 600  # newly discovered API: every 10 min
    probe_interval_healthy: int = 1800  # healthy API: every 30 min
    probe_interval_degraded: int = 300  # degraded API: every 5 min
    probe_interval_unhealthy: int = 180  # unhealthy API: every 3 min
    purge_threshold_days: int = 7  # purge after 7 days of unavailability
    probe_timeout: int = 30  # probe request timeout (seconds)
    # D3: prevent startup purge self-destruct
    purge_initial_delay_seconds: int = 86400  # first purge runs only after this delay
    skip_mock_endpoints: bool = True  # never probe/purge mock (no-key) endpoints
    # B2 review M2: dynamic (discovered) endpoints are only auto-restored from
    # purge-record snapshots when this is enabled; static config endpoints are
    # always restored (config is the source of truth).
    auto_restore_purged: bool = False


class MockConfig(BaseModel):
    """D6: Mock provider behavior — always explicit, never silent.

    mode:
      - explicit (default): requests without a real API key are served by
        MockProvider but every response is clearly labeled (X-MOA-Mock header,
        provider="mock" in usage, /health exposes mock endpoint counts).
      - disabled: requests without a real API key fail fast with 503.
        Use this in production to guarantee zero simulated output.
    """

    mode: Literal["explicit", "disabled"] = "explicit"
    header_name: str = "X-MOA-Mock"


class RateLimitConfig(BaseModel):
    enabled: bool = True
    per_key_rpm: int = 60
    per_key_daily_tokens: int = 5_000_000
    strategy: Literal["token-bucket", "sliding-window"] = "sliding-window"


class ObservabilityConfig(BaseModel):
    log_dir: str = "data/logs"
    log_json: bool = False
    metrics_enabled: bool = True
    trace_enabled: bool = False
    # T5.1: optional OTLP collector endpoint for span export (empty = in-memory
    # lightweight tracer only, spans still visible at /metrics/traces).
    otlp_endpoint: str = ""
    # P1-6: Test report configuration
    test_report_enabled: bool = True
    test_report_max_traces: int = 1000
    test_report_storage_dir: str = "data/reports"


class CacheConfig(BaseModel):
    """Cache system configuration."""

    enabled: bool = True
    exact_max_size: int = Field(default=10000, ge=100, le=1_000_000)
    exact_ttl: int = Field(default=3600, ge=60, le=604800)
    similarity_threshold: float = Field(default=0.95, ge=0.8, le=1.0)
    semantic_max_size: int = Field(default=5000, ge=100, le=500_000)
    semantic_ttl: int = Field(default=86400, ge=60, le=2592000)
    redis_url: str | None = None
    redis_prefix: str = "moa:cache:"
    null_entry_ttl: int = Field(default=30, ge=5, le=300)
    ttl_jitter_pct: float = Field(default=0.1, ge=0.0, le=0.5)
    skip_streaming: bool = True


class DiscoveryConfig(BaseModel):
    "Free model discovery system configuration."

    enabled: bool = True
    refresh_interval_hours: int = 24
    auto_configure: bool = True
    first_run_delay_seconds: int = 60
    platforms: list[str] = Field(default_factory=list)
    api_keys: dict[str, str] = Field(default_factory=dict)


class BenchmarkConfig(BaseModel):
    """Performance benchmark and capability probe configuration."""

    enabled: bool = True
    interval_seconds: int = 3600
    max_concurrent: int = 5
    probe_timeout: int = 30


class PromptTemplatesConfig(BaseModel):
    "Prompt template system configuration."

    enabled: bool = True
    custom_dir: str = "~/.moa-gateway/prompts"
    categories: list[str] = Field(
        default_factory=lambda: [
            "programming",
            "writing",
            "analysis",
            "translation",
            "summarization",
            "creative",
            "qa",
        ]
    )


class ParamTemplatesConfig(BaseModel):
    "Parameter template system configuration."

    enabled: bool = True


class AgentLoopConfig(BaseModel):
    "Agent loop configuration."

    default_loop: str = "react"
    max_iterations: int = 10
    default_tools: list[str] = Field(
        default_factory=lambda: [
            "web_search",
            "code_execute",
            "file_read",
            "file_list",
            "analyze_data",
        ]
    )


class OptimizerConfig(BaseModel):
    """MOA auto-optimiser configuration."""

    enabled: bool = True
    daily_optimization: bool = True
    max_experiments: int = 50
    convergence_threshold: float = 0.95
    test_cases_per_round: int = 3


class AssistantConfig(BaseModel):
    """Assistant API (runs) configuration (D12)."""

    # Hard cap for a single run execution (LLM round-trips included).
    # Runs exceeding this are marked failed with code "timeout".
    run_timeout_seconds: float = 300.0
    # Timeout for one internal gateway LLM round-trip inside a run.
    llm_call_timeout_seconds: float = 120.0


class MCPConfig(BaseModel):
    """MCP external-server configuration.

    Security model for the stdio launcher (spawning external MCP servers as
    child processes):
    - ``stdio_allowed_commands`` is a strict allowlist of executable names.
      Any command whose basename is not in this list is refused before the
      subprocess is ever spawned.
    - ``stdio_strip_secret_env`` removes the gateway's own secret variables
      from the child environment so a third-party MCP server cannot read
      admin credentials / signing keys via ``os.environ``.
    """

    stdio_allowed_commands: list[str] = Field(
        default_factory=lambda: ["python", "python3", "node", "npx", "uvx"]
    )
    stdio_strip_secret_env: bool = True
    # Default per-request JSON-RPC timeout (seconds) for stdio clients.
    stdio_request_timeout: float = Field(default=30.0, gt=0)
    # Grace period (seconds) for a stdio server to exit on shutdown before kill.
    stdio_shutdown_timeout: float = Field(default=5.0, gt=0)


class CLIConfig(BaseModel):
    """External CLI tool registry / execution sandbox configuration.

    Security model (allowlist-only, mirrors the MCP stdio launcher):
    - ``allowed_executables``: only these program names may ever be spawned.
      Registering any tool whose argv[0] basename is not on the list is
      rejected. Admins extend the list via config.yaml — never per-request.
    - ``sandbox_dir``: default working directory for spawned children (under
      the data dir, auto-created). A tool may only use another cwd if it is
      whitelisted via ``allowed_dirs``.
    - argv is always passed to subprocess as a list (never a shell string)
      and the child env is scrubbed of gateway secrets before spawn
      (see capability/cli_registry.py).
    """

    enabled: bool = True
    allowed_executables: list[str] = Field(
        default_factory=lambda: ["python", "python3", "git", "node", "curl"]
    )
    sandbox_dir: str = "data/cli_sandbox"
    # Extra directories (absolute or ROOT_DIR-relative) a tool may use as cwd.
    allowed_dirs: list[str] = Field(default_factory=list)
    default_timeout_s: float = Field(30.0, gt=0)
    max_timeout_s: float = Field(300.0, gt=0)
    max_output_bytes: int = Field(1_000_000, gt=0)
    # Hard ceiling for a single tool's registered max_output_bytes.
    max_output_bytes_cap: int = Field(10_000_000, gt=0)


# ============================================================================
# v4.1.0 integration configs — derived from three MIT-licensed projects:
# OmniRoute (routing/quota/compression/free-tiers/A2A), OpenClacky
# (token efficiency/skills/subagents/channels), MemoraX Code (memory).
# Attribution: see THIRD_PARTY_NOTICES.md.
# ============================================================================


class RoutingStrategiesConfig(BaseModel):
    """OmniRoute-style routing strategy engine (19 public + quota-share)."""

    enabled: bool = True
    default_strategy: str = "auto"
    # Sliding window size for per-endpoint latency/success statistics.
    history_window: int = Field(default=100, ge=1)
    # Auto-strategy factor weights (OmniRoute autoCombo defaults, renormalised).
    auto_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "quota": 0.143,
            "health": 0.161,
            "cost_inv": 0.143,
            "latency_inv": 0.114,
            "task_fit": 0.076,
            "stability": 0.048,
            "tier": 0.048,
            "specificity": 0.048,
            "context_affinity": 0.048,
            "session_avail": 0.048,
            "conn_density": 0.048,
            "quality": 0.030,
        }
    )


class QuotaSchedulerConfig(BaseModel):
    """OmniRoute-style quota telemetry + quota-aware scheduling."""

    enabled: bool = True
    # Adaptive monitor cadence: normal -> fast when approaching limits.
    poll_interval_s: float = Field(default=60.0, gt=0)
    fast_poll_interval_s: float = Field(default=15.0, gt=0)
    warn_threshold: float = Field(default=0.80, ge=0, le=1)
    exhaust_threshold: float = Field(default=0.95, ge=0, le=1)
    # Fail-open: quota uncertainty never blocks requests (OmniRoute policy).
    fail_open: bool = True
    max_snapshots: int = Field(default=5000, ge=100)


class CompressionConfig(BaseModel):
    """OmniRTK/Caveman stacked prompt-compression pipeline.

    ``apply_to_chat`` is opt-in (default False): mutating request payloads by
    default would silently alter legitimate traffic (OmniRoute hard rule #20).
    """

    enabled: bool = True
    apply_to_chat: bool = False
    default_mode: str = "off"  # off|lite|standard|aggressive|ultra|rtk|stacked
    # Fidelity gate: reject a compression result that drops protected blocks.
    fidelity_gate: bool = True
    # Preserve blocks carrying provider cache markers byte-for-byte.
    preserve_cache_control: bool = True
    hard_budget_chars: int = Field(default=200_000, gt=0)
    max_input_chars: int = Field(default=1_000_000, gt=0)


class FreeTiersConfig(BaseModel):
    """Free-tier catalog (OmniRoute catalog data, pool-deduped aggregation)."""

    enabled: bool = True
    # Empty = use bundled catalog resource shipped with the package.
    catalog_path: str = ""


class A2AConfig(BaseModel):
    """Agent-to-Agent (A2A) JSON-RPC 2.0 surface + Agent Card."""

    enabled: bool = True
    agent_name: str = "moa-gateway-pro"
    agent_description: str = (
        "Industrial multi-model collaboration gateway: routing, MoA, quota-aware "
        "scheduling, compression, skills and cross-session memory."
    )
    agent_version: str = "4.2.0"


class EfficiencyConfig(BaseModel):
    """OpenClacky-style token-efficiency harness for conversational sessions."""

    enabled: bool = True
    # Anthropic-style double cache markers on the two trailing messages.
    cache_markers: bool = True
    # Compression triggers (OpenClacky defaults).
    compression_threshold_tokens: int = Field(default=150_000, gt=0)
    compression_threshold_messages: int = Field(default=200, gt=0)
    # Idle compression runs before the typical 5-min provider cache TTL so the
    # compression call itself hits the warm cache (OpenClacky: 266s).
    idle_delay_s: float = Field(default=266.0, gt=0)
    idle_threshold_tokens: int = Field(default=20_000, gt=0)
    target_compressed_tokens: int = Field(default=10_000, gt=0)
    max_recent_messages: int = Field(default=20, gt=0)
    # Raw history archive (markdown chunks) kept under the data dir.
    archive_dir: str = "data/session_archives"


class SkillHubConfig(BaseModel):
    """OpenClacky-style skill ecosystem (SKILL.md + invoke_skill meta tool)."""

    enabled: bool = True
    # Extra skill directories scanned after the bundled set (first wins).
    extra_dirs: list[str] = Field(default_factory=list)
    max_skills_in_prompt: int = Field(default=30, ge=1)
    # Self-evolution: reflect & rewrite a skill after N+ iterations of use.
    evolution_enabled: bool = True
    evolution_min_iterations: int = Field(default=5, ge=1)
    # Auto-create a new skill when a skill-less task iterates N+ times.
    auto_create_min_iterations: int = Field(default=12, ge=1)


class ChannelsConfig(BaseModel):
    """OpenClacky-style IM channel layer (Telegram/Feishu/DingTalk/WeCom/Discord)."""

    enabled: bool = True
    poll_interval_s: float = Field(default=3.0, gt=0)
    max_message_chars: int = Field(default=8000, gt=0)
    session_ttl_s: float = Field(default=3600.0, gt=0)
    # Credentials come from env only (never config files):
    #   MOA_TELEGRAM_BOT_TOKEN / MOA_FEISHU_APP_SECRET / MOA_DINGTALK_SECRET ...
    env_prefix: str = "MOA_"


class MemoryConfig(BaseModel):
    """MemoraX-style cross-session memory layer.

    Retrieval injection and writeback are both opt-in (default False):
    the gateway must never mutate conversational traffic silently.
    """

    enabled: bool = True
    retrieval_enabled: bool = False
    writeback_enabled: bool = False
    # Recall recipe (MemoraX defaults): small top_k + score floor + char budget.
    top_k: int = Field(default=6, ge=1)
    min_score: float = Field(default=0.0, ge=0, le=1)
    max_context_chars: int = Field(default=4000, gt=0)
    max_item_chars: int = Field(default=1000, gt=0)
    memory_type_order: list[str] = Field(
        default_factory=lambda: ["core", "episodic", "semantic", "procedural"]
    )
    # Writeback pipeline: buffer -> chunk -> idempotent store.
    buffer_turns: int = Field(default=8, ge=1)
    buffer_seconds: float = Field(default=600.0, gt=0)
    buffer_chars: int = Field(default=131_072, gt=0)
    chunk_chars: int = Field(default=8000, gt=100)
    chunk_overlap: float = Field(default=0.05, ge=0, le=0.5)
    redact_pii: bool = True
    # Workspace (repo-style) memory: .moa_memory knowledge layer.
    workspace_enabled: bool = False
    workspace_update_policy: str = "adaptive"  # adaptive|every_commit|commit_count|daily
    workspace_commit_threshold: int = Field(default=5, ge=1)
    workspace_cooldown_hours: float = Field(default=24.0, gt=0)


class Settings(BaseModel):
    """全局配置 — root model"""

    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    models: list[ModelEndpointConfig] = Field(default_factory=list)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    moa: MoAConfig = Field(default_factory=MoAConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    mock: MockConfig = Field(default_factory=MockConfig)
    ratelimit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    # === Discovery & Template System ===
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    prompt_templates: PromptTemplatesConfig = Field(default_factory=PromptTemplatesConfig)
    param_templates: ParamTemplatesConfig = Field(default_factory=ParamTemplatesConfig)
    agent_loop: AgentLoopConfig = Field(default_factory=AgentLoopConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    cli: CLIConfig = Field(default_factory=CLIConfig)

    # === v4.1.0 integration (OmniRoute / OpenClacky / MemoraX Code) ===
    routing_strategies: RoutingStrategiesConfig = Field(default_factory=RoutingStrategiesConfig)
    quota: QuotaSchedulerConfig = Field(default_factory=QuotaSchedulerConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    free_tiers: FreeTiersConfig = Field(default_factory=FreeTiersConfig)
    a2a: A2AConfig = Field(default_factory=A2AConfig)
    efficiency: EfficiencyConfig = Field(default_factory=EfficiencyConfig)
    skillhub: SkillHubConfig = Field(default_factory=SkillHubConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


def _ensure_jwt_secret(cfg: Settings) -> Settings:
    """确保 JWT secret 存在,首次启动自动生成并写入磁盘(0600权限)"""
    import os as _os
    secret_path = DATA_DIR / ".jwt_secret"
    if not cfg.auth.jwt_secret:
        if secret_path.exists():
            cfg.auth.jwt_secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            new_secret = secrets.token_urlsafe(48)
            fd = _os.open(str(secret_path), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            _os.write(fd, new_secret.encode("utf-8"))
            _os.close(fd)
            cfg.auth.jwt_secret = new_secret
            logger.info("Generated new JWT secret and saved to %s (mode 0600)", secret_path)
    return cfg


def _resolve_api_keys(cfg: Settings) -> Settings:
    """从环境变量注入每个端点的 api_key_runtime"""
    import os

    for m in cfg.models:
        if m.api_key:
            m.api_key_runtime = m.api_key
        elif m.api_key_env:
            m.api_key_runtime = os.getenv(m.api_key_env, "")
        else:
            m.api_key_runtime = ""
        # 如果是从 yaml 同步过来的 disabled 默认值,允许 env 存在则自动 enable
        if m.api_key_runtime and not m.enabled:
            # 不强制 enable,留个口子:env 有 key 就视为"可用",但 enabled 还是人工控制
            pass
    return cfg


def load_settings(config_path: Path | None = None) -> Settings:
    """加载完整配置"""
    config_path = config_path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        logger.info("Loaded base config from %s", config_path)
    else:
        logger.warning("Config file %s not found, using defaults", config_path)

    cfg = Settings(**raw)
    # Task #35: Expand ~ in prompt_templates.custom_dir
    if cfg.prompt_templates.custom_dir:
        cfg.prompt_templates.custom_dir = os.path.expanduser(cfg.prompt_templates.custom_dir)
    # 修26: 安全加固 — auth.admin_password 留空时强制用 env var 兜底
    # 优先级:env MOA_ADMIN_PASSWORD > yaml admin_password
    env_pw = os.environ.get("MOA_ADMIN_PASSWORD", "").strip()
    if not cfg.auth.admin_password.strip() and env_pw:
        cfg.auth.admin_password = env_pw
        logger.info("admin_password loaded from MOA_ADMIN_PASSWORD env var")
    # Container/12-factor support: env overrides for host/port/workers so the
    # Dockerfile's ENV MOA_HOST/MOA_PORT actually take effect.
    env_host = os.environ.get("MOA_HOST", "").strip()
    if env_host:
        cfg.server.host = env_host
    env_port = os.environ.get("MOA_PORT", "").strip()
    if env_port.isdigit():
        cfg.server.port = int(env_port)
    env_workers = os.environ.get("MOA_WORKERS", "").strip()
    if env_workers.isdigit():
        cfg.server.workers = int(env_workers)
    cfg = _ensure_jwt_secret(cfg)
    cfg = _resolve_api_keys(cfg)
    return cfg


# 全局单例 + 订阅(修19)
_settings: Settings | None = None
_settings_subscribers: list = []


def get_settings() -> Settings:
    """获取全局配置(惰性加载)"""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reload_settings() -> Settings:
    """重新加载配置 + 通知订阅者"""
    global _settings
    old = _settings
    _settings = load_settings()
    # 通知订阅者(模型池、路由器、限流器等)
    for cb in list(_settings_subscribers):
        try:
            cb(old, _settings)
        except Exception as e:
            logger.warning("settings subscriber error: %s", e)
    return _settings


def subscribe_settings_change(callback):
    """订阅 settings 热更。callback(old, new)"""
    _settings_subscribers.append(callback)


def apply_db_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """应用数据库里存的覆盖配置(用于热更新)"""
    if not overrides:
        return settings
    base = settings.model_dump()
    _deep_merge(base, overrides)
    new_settings = Settings(**base)
    new_settings = _ensure_jwt_secret(new_settings)
    new_settings = _resolve_api_keys(new_settings)
    return new_settings


def _deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base
