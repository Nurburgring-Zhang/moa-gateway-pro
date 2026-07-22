"""Initial schema - baseline from SQLite

Revision ID: 001
Revises: None
Create Date: 2026-07-21

This migration creates the baseline schema that matches the existing
SQLite database structure. It's compatible with both SQLite and PostgreSQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables (PostgreSQL-compatible DDL)."""

    # Admin users
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_login", sa.Float(), nullable=True),
    )

    # Login attempts (brute-force protection)
    op.create_table(
        "login_attempts",
        sa.Column("ip", sa.String(45), primary_key=True),
        sa.Column("count", sa.Integer(), server_default="0"),
        sa.Column("window_start", sa.Float(), nullable=False),
    )

    # API Keys
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_id", sa.String(255), unique=True, nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("quota_rpm", sa.Integer(), server_default="60"),
        sa.Column("quota_daily_tokens", sa.Integer(), server_default="5000000"),
        sa.Column("enabled", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_used", sa.Float(), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
    )
    op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"])

    # Model endpoints
    op.create_table(
        "model_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("endpoint_id", sa.String(255), unique=True, nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("tier", sa.String(50), nullable=False),
        sa.Column("api_base", sa.Text(), nullable=True),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_env", sa.String(255), nullable=True),
        sa.Column("cost_per_1k_input", sa.Float(), server_default="0.001"),
        sa.Column("cost_per_1k_output", sa.Float(), server_default="0.002"),
        sa.Column("max_tokens", sa.Integer(), server_default="8192"),
        sa.Column("timeout", sa.Integer(), server_default="120"),
        sa.Column("weight", sa.Integer(), server_default="100"),
        sa.Column("enabled", sa.Integer(), server_default="0"),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )

    # Request logs
    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("api_key_id", sa.String(255), nullable=True),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("model_requested", sa.Text(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("preset", sa.String(100), nullable=True),
        sa.Column("strategy", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), server_default="0"),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost", sa.Float(), server_default="0"),
        sa.Column("latency_ms", sa.Float(), server_default="0"),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("consensus_score", sa.Float(), nullable=True),
        sa.Column("fallback_used", sa.Integer(), server_default="0"),
        sa.Column("metadata", sa.Text(), nullable=True),
    )
    op.create_index("idx_request_logs_ts", "request_logs", ["timestamp"])
    op.create_index("idx_request_logs_apikey", "request_logs", ["api_key_id"])
    op.create_index("idx_request_logs_status_ts", "request_logs", ["status", "timestamp"])
    op.create_index("idx_request_logs_model_ts", "request_logs", ["model_used", "timestamp"])

    # Config overrides
    op.create_table(
        "config_overrides",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )

    # Rate limit buckets
    op.create_table(
        "ratelimit_buckets",
        sa.Column("api_key_id", sa.String(255), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("api_key_id", "bucket"),
    )

    # Rate limit tokens
    op.create_table(
        "ratelimit_tokens",
        sa.Column("api_key_id", sa.String(255), nullable=False),
        sa.Column("day", sa.String(8), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("api_key_id", "day"),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("ratelimit_tokens")
    op.drop_table("ratelimit_buckets")
    op.drop_table("config_overrides")
    op.drop_table("request_logs")
    op.drop_table("model_endpoints")
    op.drop_table("api_keys")
    op.drop_table("login_attempts")
    op.drop_table("admin_users")
