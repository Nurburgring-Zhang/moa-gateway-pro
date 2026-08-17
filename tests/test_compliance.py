"""SOC2 Compliance Module Tests — encryption, PII, audit integrity, GDPR, baseline."""
from __future__ import annotations

import os
import time

import pytest


# ========== Encryption Tests ==========
class TestFieldEncryptor:
    """Test AES-256-GCM field-level encryption."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypted text decrypts back to original."""
        from moa_gateway.compliance.encryption import FieldEncryptor

        enc = FieldEncryptor("test-master-key-for-soc2")
        plaintext = "sensitive-api-key-12345"
        ciphertext = enc.encrypt(plaintext)

        assert ciphertext.startswith("ENC:")
        assert ciphertext != plaintext
        assert enc.decrypt(ciphertext) == plaintext

    def test_encrypt_different_nonces(self):
        """Same plaintext produces different ciphertexts (random nonce)."""
        from moa_gateway.compliance.encryption import FieldEncryptor

        enc = FieldEncryptor("test-key")
        ct1 = enc.encrypt("hello")
        ct2 = enc.encrypt("hello")
        # Should be different due to random nonce
        assert ct1 != ct2
        # But both decrypt to same value
        assert enc.decrypt(ct1) == "hello"
        assert enc.decrypt(ct2) == "hello"

    def test_encrypt_disabled_without_key(self):
        """Encryptor is disabled when no key is provided."""
        from moa_gateway.compliance.encryption import FieldEncryptor

        enc = FieldEncryptor("")
        assert not enc.enabled
        assert enc.encrypt("hello") == "hello"

    def test_decrypt_non_encrypted_passthrough(self):
        """Non-encrypted strings pass through unchanged."""
        from moa_gateway.compliance.encryption import FieldEncryptor

        enc = FieldEncryptor("key")
        assert enc.decrypt("plain text") == "plain text"
        assert enc.decrypt("") == ""

    def test_encrypt_unicode(self):
        """Unicode text encrypts/decrypts correctly."""
        from moa_gateway.compliance.encryption import FieldEncryptor

        enc = FieldEncryptor("unicode-key")
        text = "用户密码：P@ssw0rd!中文测试"
        ct = enc.encrypt(text)
        assert enc.decrypt(ct) == text


# ========== PII Detection Tests ==========
class TestPIIDetector:
    """Test PII detection patterns."""

    def test_detect_email(self):
        """Detects email addresses."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("Contact us at admin@example.com for help")
        assert len(matches) >= 1
        assert any(m.type == "email" for m in matches)
        email_match = next(m for m in matches if m.type == "email")
        assert "***" in email_match.masked

    def test_detect_chinese_phone(self):
        """Detects Chinese mobile numbers."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("手机号 13912345678 请联系")
        assert any(m.type == "phone_cn" for m in matches)

    def test_detect_credit_card(self):
        """Detects credit card numbers."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("Card: 4111111111111111 exp 12/25")
        assert any(m.type == "credit_card" for m in matches)

    def test_detect_ssn(self):
        """Detects US SSN format."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("SSN: 123-45-6789")
        assert any(m.type == "ssn_us" for m in matches)

    def test_detect_ip_address(self):
        """Detects IP addresses."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("Client IP: 192.168.1.100")
        assert any(m.type == "ip_address" for m in matches)

    def test_redact_multiple_pii(self):
        """Redacts multiple PII types from text."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        text = "User test@mail.com called from 13800138000"
        redacted = d.redact(text)
        assert "test@mail.com" not in redacted
        assert "13800138000" not in redacted
        assert "***" in redacted

    def test_no_pii(self):
        """No false positives on clean text."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        matches = d.detect("This is a normal message without PII")
        assert len(matches) == 0

    def test_has_pii_convenience(self):
        """has_pii() quick check works."""
        from moa_gateway.compliance.pii_detector import PIIDetector

        d = PIIDetector()
        assert d.has_pii("email: a@b.com")
        assert not d.has_pii("no pii here")


# ========== Audit Integrity Tests ==========
class TestAuditIntegrity:
    """Test HMAC signature chain."""

    def test_sign_and_verify_entry(self):
        """Signed entry can be verified."""
        from moa_gateway.compliance.audit_integrity import AuditIntegrity

        ai = AuditIntegrity("test-signing-key")
        entry = {"action": "login", "user": "admin", "ip": "10.0.0.1"}
        signed = ai.sign_entry(entry)

        assert "_hash" in signed
        assert "_seq" in signed
        assert "_prev_hash" in signed
        assert signed["_prev_hash"] == "GENESIS"

        # Verify
        assert ai.verify_entry(dict(signed))

    def test_chain_integrity(self):
        """Chain of entries verifies correctly."""
        from moa_gateway.compliance.audit_integrity import AuditIntegrity

        ai = AuditIntegrity("chain-key")
        entries = []
        for i in range(5):
            e = ai.sign_entry({"action": f"action_{i}", "seq": i})
            entries.append(e)

        valid, count, total = ai.verify_chain(entries)
        assert valid is True
        assert count == 5
        assert total == 5

    def test_tamper_detection(self):
        """Tampered entry fails verification."""
        from moa_gateway.compliance.audit_integrity import AuditIntegrity

        ai = AuditIntegrity("tamper-key")
        entry = ai.sign_entry({"action": "transfer", "amount": 1000})

        # Tamper with the entry
        tampered = dict(entry)
        tampered["amount"] = 999999
        assert not ai.verify_entry(tampered)

    def test_chain_break_detection(self):
        """Broken chain (missing entry) is detected."""
        from moa_gateway.compliance.audit_integrity import AuditIntegrity

        ai = AuditIntegrity("break-key")
        e1 = ai.sign_entry({"action": "a"})
        _e2 = ai.sign_entry({"action": "b"})  # noqa: F841 - needed to advance chain
        e3 = ai.sign_entry({"action": "c"})

        # Skip e2 in chain
        ai2 = AuditIntegrity("break-key")
        valid, count, total = ai2.verify_chain([e1, e3])
        # e3 should fail because prev_hash won't match e1's hash
        assert valid is False


# ========== Key Rotation Tests ==========
class TestKeyRotation:
    """Test key rotation management."""

    def test_generate_key(self):
        """Can generate a new key."""
        from moa_gateway.compliance.key_rotation import KeyRotationManager

        mgr = KeyRotationManager()
        key = mgr.generate_key("api")
        assert key.is_primary
        assert key.key_id.startswith("api-")
        assert len(key.key_value) > 20

    def test_rotation_demotes_old_primary(self):
        """New key generation demotes old primary."""
        from moa_gateway.compliance.key_rotation import KeyRotationManager

        mgr = KeyRotationManager()
        k1 = mgr.generate_key("api")
        assert k1.is_primary
        k2 = mgr.generate_key("api")
        assert k2.is_primary
        assert not k1.is_primary

    def test_should_rotate_no_key(self):
        """should_rotate returns True when no key exists."""
        from moa_gateway.compliance.key_rotation import KeyRotationManager

        mgr = KeyRotationManager()
        assert mgr.should_rotate()

    def test_key_status(self):
        """Status report is complete."""
        from moa_gateway.compliance.key_rotation import KeyRotationManager

        mgr = KeyRotationManager()
        mgr.generate_key("api")
        status = mgr.get_status()
        assert status["total_keys"] == 1
        assert status["primary_key_id"] is not None
        assert "rotation_needed" in status


# ========== GDPR Tests ==========
class TestGDPR:
    """Test GDPR data subject rights."""

    @pytest.mark.asyncio
    async def test_create_deletion_request(self):
        """Can create a GDPR deletion request."""
        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        req = await mgr.create_deletion_request("user-123")
        assert req.user_id == "user-123"
        assert req.status == "pending"
        assert req.request_id

    @pytest.mark.asyncio
    async def test_process_deletion(self):
        """Can process a deletion request."""
        import sqlite3

        from moa_gateway.compliance.gdpr import GDPRManager

        # v3.1.1: GDPR deletion targets the REAL schema (admin_users, and
        # api_keys/request_logs keyed by key_id) — mirror it exactly.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE admin_users (username TEXT)")
        conn.execute("CREATE TABLE request_logs (api_key_id TEXT)")
        conn.execute("CREATE TABLE api_keys (key_id TEXT, name TEXT)")
        conn.execute("INSERT INTO admin_users VALUES ('user-456')")
        conn.execute("INSERT INTO api_keys VALUES ('key_1', 'user-456')")
        conn.execute("INSERT INTO request_logs VALUES ('key_1')")

        mgr = GDPRManager()
        req = await mgr.create_deletion_request("user-456")
        result = await mgr.process_deletion(req.request_id, db_conn=conn)
        assert result["status"] == "completed", result
        assert req.status == "completed"
        # the user row must actually be gone (right to be forgotten)
        left = conn.execute(
            "SELECT COUNT(*) FROM admin_users WHERE username = 'user-456'"
        ).fetchone()[0]
        assert left == 0
        # and the log row must be anonymized, not left attributable
        log_key = conn.execute("SELECT api_key_id FROM request_logs").fetchone()[0]
        assert log_key.startswith("anon_")
        conn.close()

    @pytest.mark.asyncio
    async def test_export_user_data(self):
        """Can export user data."""
        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        data = await mgr.export_user_data("user-789")
        assert data["user_id"] == "user-789"
        assert data["format"] == "json"
        assert "data" in data

    @pytest.mark.asyncio
    async def test_deletion_request_not_found(self):
        """Returns error for non-existent request."""
        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        result = await mgr.process_deletion("nonexistent-id")
        assert result["status"] == "not_found"

    def test_get_request_status(self):
        """Can query request status."""
        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        assert mgr.get_request_status("nonexistent") is None


# ========== Data Retention Tests ==========
class TestDataRetention:
    """Test data retention policies."""

    def test_default_policies(self):
        """Default policies are configured."""
        from moa_gateway.compliance.data_retention import DataRetentionManager

        mgr = DataRetentionManager()
        policies = mgr.get_policy_status()
        assert len(policies) >= 4
        names = [p["name"] for p in policies]
        assert "audit_logs" in names
        assert "request_logs" in names

    @pytest.mark.asyncio
    async def test_cleanup_files(self, tmp_path):
        """File cleanup removes old files."""
        from moa_gateway.compliance.data_retention import DataRetentionManager

        # Create test files
        old_file = tmp_path / "old.txt"
        old_file.write_text("old data")
        # Set modification time to 100 days ago
        old_time = time.time() - 100 * 86400
        os.utime(old_file, (old_time, old_time))

        new_file = tmp_path / "new.txt"
        new_file.write_text("new data")

        from moa_gateway.compliance.data_retention import RetentionPolicy
        mgr = DataRetentionManager(policies=[
            RetentionPolicy("test", 30, str(tmp_path) + "/", ""),
        ])
        results = await mgr.run_cleanup()
        assert results["test"]["status"] == "ok"
        assert results["test"]["deleted"] == 1
        assert not old_file.exists()
        assert new_file.exists()


# ========== Security Baseline Tests ==========
class TestSecurityBaseline:
    """Test security configuration baseline checks."""

    def test_all_checks_run(self):
        """All 10 baseline checks execute."""
        from moa_gateway.compliance.baseline_check import SecurityBaselineChecker

        checker = SecurityBaselineChecker()
        results = checker.run_all_checks()
        assert len(results) == 10

    def test_summary_format(self):
        """Summary returns expected structure."""
        from moa_gateway.compliance.baseline_check import SecurityBaselineChecker

        checker = SecurityBaselineChecker()
        summary = checker.summary()
        assert "total_checks" in summary
        assert "passed" in summary
        assert "failed" in summary
        assert "critical_failures" in summary
        assert "results" in summary
        assert summary["total_checks"] == 10

    def test_jwt_secret_check_fails_without_env(self, monkeypatch):
        """JWT secret check fails when not configured."""
        from moa_gateway.compliance.baseline_check import SecurityBaselineChecker

        monkeypatch.delenv("MOA_JWT_SECRET", raising=False)
        checker = SecurityBaselineChecker()
        results = checker.run_all_checks()
        jwt_check = next(r for r in results if r.name == "jwt_secret")
        assert not jwt_check.passed
        assert jwt_check.severity == "critical"

    def test_jwt_secret_check_passes(self, monkeypatch):
        """JWT secret check passes with strong secret."""
        from moa_gateway.compliance.baseline_check import SecurityBaselineChecker

        monkeypatch.setenv("MOA_JWT_SECRET", "a" * 64)
        checker = SecurityBaselineChecker()
        results = checker.run_all_checks()
        jwt_check = next(r for r in results if r.name == "jwt_secret")
        assert jwt_check.passed

    def test_debug_mode_check(self, monkeypatch):
        """Debug mode check detects enabled debug."""
        from moa_gateway.compliance.baseline_check import SecurityBaselineChecker

        monkeypatch.setenv("MOA_DEBUG", "true")
        checker = SecurityBaselineChecker()
        results = checker.run_all_checks()
        debug_check = next(r for r in results if r.name == "debug_mode")
        assert not debug_check.passed
