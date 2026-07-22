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
    admin_password: str = "admin"
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
    interval_seconds: int = 30
    timeout_seconds: int = 10
    failure_threshold: int = 3
    cooldown_seconds: int = 60
    healthy_recheck: int = 120


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

class Settings(BaseModel):
    """全局配置 — root model"""

    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    models: list[ModelEndpointConfig] = Field(default_factory=list)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    moa: MoAConfig = Field(default_factory=MoAConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    ratelimit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


def _ensure_jwt_secret(cfg: Settings) -> Settings:
    """确保 JWT secret 存在,首次启动自动生成并写入磁盘"""
    secret_path = DATA_DIR / ".jwt_secret"
    if not cfg.auth.jwt_secret:
        if secret_path.exists():
            cfg.auth.jwt_secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            new_secret = secrets.token_urlsafe(48)
            secret_path.write_text(new_secret, encoding="utf-8")
            cfg.auth.jwt_secret = new_secret
            logger.info("Generated new JWT secret and saved to %s", secret_path)
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
    # 修26: 安全加固 — auth.admin_password 留空时强制用 env var 兜底
    # 优先级:env MOA_ADMIN_PASSWORD > yaml admin_password
    env_pw = os.environ.get("MOA_ADMIN_PASSWORD", "").strip()
    if not cfg.auth.admin_password.strip() and env_pw:
        cfg.auth.admin_password = env_pw
        logger.info("admin_password loaded from MOA_ADMIN_PASSWORD env var")
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
