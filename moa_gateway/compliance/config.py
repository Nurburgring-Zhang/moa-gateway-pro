"""Compliance configuration constants."""
import os

# Encryption
ENCRYPTION_KEY = os.getenv("MOA_ENCRYPTION_KEY", "")
ENCRYPTION_ALGORITHM = "AES-256-GCM"

# Audit integrity
AUDIT_SIGNING_KEY = os.getenv("MOA_AUDIT_SIGNING_KEY", "audit-default-key")

# Key rotation
KEY_ROTATION_DAYS = int(os.getenv("MOA_KEY_ROTATION_DAYS", "90"))
KEY_STORE_PATH = os.getenv("MOA_KEY_STORE", "data/key_versions.json")

# Data retention (days)
RETENTION_AUDIT_LOGS = int(os.getenv("MOA_RETENTION_AUDIT_DAYS", "90"))
RETENTION_REQUEST_LOGS = int(os.getenv("MOA_RETENTION_REQUEST_DAYS", "30"))
RETENTION_CACHE = int(os.getenv("MOA_RETENTION_CACHE_DAYS", "7"))
RETENTION_SESSIONS = int(os.getenv("MOA_RETENTION_SESSION_DAYS", "1"))

# GDPR
GDPR_DELETION_GRACE_DAYS = int(os.getenv("MOA_GDPR_GRACE_DAYS", "30"))

# PII detection
PII_ENABLED = os.getenv("MOA_PII_DETECTION", "true").lower() in ("true", "1", "yes")
PII_LOG_REDACTION = os.getenv("MOA_PII_LOG_REDACTION", "true").lower() in ("true", "1", "yes")
