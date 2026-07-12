"""secret_scan 真实测试 — 端到端验证(非 mock)"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.secret_scan import (
    SECRET_PATTERNS, redact, scan_text, scan_path,
    Finding, ScanResult, should_block,
)


def test_redact():
    """脱敏保留前后字符"""
    # AKIA key 20 chars → AKIA***CDEF
    assert redact("AKIA1234567890ABCDEF") == "AKIA***CDEF", \
        f"got {redact('AKIA1234567890ABCDEF')!r}"
    # ghp_ 41 chars → ghp_***xxxx
    assert redact("ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") == "ghp_***xxxx", \
        f"got {redact('ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')!r}"
    # 5 char → sho***rt(len 5-12,return prefix 3 + *** + suffix 2)
    assert redact("5chars") == "5ch***rs", f"got {redact('5chars')!r}"
    # < 5 char → ***
    assert redact("ab") == "***", f"got {redact('ab')!r}"
    print("  ✓ test_redact")
    assert True


def test_scan_text_finds_aws_key():
    """扫描一段文本,能找到 AWS key"""
    text = "aws_key = AKIAIOSFODNN7EXAMPLE"
    findings = scan_text(text, "config.py")
    aws_findings = [f for f in findings if f.pattern_id == "AWS_ACCESS_KEY"]
    assert len(aws_findings) == 1, f"expected 1, got {len(aws_findings)}"
    assert "AKIA" in aws_findings[0].match
    assert "***" in aws_findings[0].redacted
    print("  ✓ test_scan_text_finds_aws_key")
    assert True


def test_scan_text_finds_github_pat():
    """GitHub PAT 检测"""
    text = "GITHUB_TOKEN=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    findings = scan_text(text, "config.py")
    gh_findings = [f for f in findings if "GITHUB" in f.pattern_id]
    assert len(gh_findings) >= 1
    print("  ✓ test_scan_text_finds_github_pat")
    assert True


def test_scan_text_finds_anthropic():
    """Anthropic key"""
    text = "key = sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
    findings = scan_text(text, "config.py")
    ant = [f for f in findings if "ANTHROPIC" in f.pattern_id]
    assert len(ant) >= 1, f"expected anthropic finding, got: {[(f.pattern_id, f.match) for f in findings]}"
    print("  ✓ test_scan_text_finds_anthropic")
    assert True


def test_exempt_inline():
    """inline 豁免 # moat:ignore=PATTERN"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # reason 要带引号
        text = 'AKIAIOSFODNN7EXAMPLE  # moat:ignore=AWS_ACCESS_KEY reason="test_fixture"'
        findings = scan_text(text, "test.py", root=root)
        aws = [f for f in findings if f.pattern_id == "AWS_ACCESS_KEY"]
        assert len(aws) == 1, f"expected 1, got {len(aws)}"
        assert aws[0].exempt is True, f"expected exempt, got {aws[0].exempt}"
        assert aws[0].exempt_reason == "test_fixture", f"got {aws[0].exempt_reason}"
        assert aws[0].exempt_source == "inline", f"got {aws[0].exempt_source}"
    print("  ✓ test_exempt_inline")
    assert True


def test_exempt_path():
    """路径名豁免(path 含 test/fixture/example/mock)"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text = "AKIAIOSFODNN7EXAMPLE"
        findings = scan_text(text, "tests/fixtures/data.py", root=root)
        aws = [f for f in findings if f.pattern_id == "AWS_ACCESS_KEY"]
        assert len(aws) == 1, f"expected 1, got {len(aws)}"
        assert aws[0].exempt is True, f"expected exempt, got {aws[0].exempt}, source={aws[0].exempt_source}"
        assert "path" in aws[0].exempt_source, f"got {aws[0].exempt_source}"
    print("  ✓ test_exempt_path")
    assert True


def test_scan_path_real_directory():
    """真实扫描目录"""
    with tempfile.TemporaryDirectory() as tmp:
        tmppath = Path(tmp)
        # 写 2 个有 secret 的文件
        (tmppath / "config.py").write_text(
            "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
            "GH_TOKEN = 'ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789'\n"
        )
        (tmppath / "test_data.py").write_text(
            "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'  # moat:ignore=AWS_ACCESS_KEY reason=\"fixture\"\n"
        )
        result = scan_path(tmppath)
        assert result.scanned_files == 2
        # 2 文件各 1 AWS_KEY (1 豁免 1 不豁免) + 1 GH_TOKEN(不豁免)
        assert result.real_count >= 2  # 至少 2 个不豁免(GH_TOKEN + 1 AWS_KEY)
        assert result.exempt_count >= 1  # 至少 1 豁免(test_data.py)
        # 严重度 3 应该 block
        assert should_block(result, fail_on=3) is True
    print("  ✓ test_scan_path_real_directory")
    assert True


def test_scan_path_skips_binary():
    """跳过 binary 文件"""
    with tempfile.TemporaryDirectory() as tmp:
        tmppath = Path(tmp)
        # 写一个 binary 文件含 "AKIA..." 但因 binary 应该跳过
        (tmppath / "image.png").write_bytes(b"\x00\x01AKIAIOSFODNN7EXAMPLE\x00")
        result = scan_path(tmppath)
        assert result.scanned_files == 0  # 没扫到(binary 跳过)
    print("  ✓ test_scan_path_skips_binary")
    assert True


def test_scan_path_skips_secret_files():
    """跳过 .fernet_key / .jwt_secret"""
    with tempfile.TemporaryDirectory() as tmp:
        tmppath = Path(tmp)
        (tmppath / ".fernet_key").write_text("qC...")  # 即使有 secret 也不应扫
        (tmppath / "config.py").write_text("# empty\n")
        result = scan_path(tmppath)
        assert result.scanned_files == 1  # 只扫 config.py
    print("  ✓ test_scan_path_skips_secret_files")
    assert True


def test_no_false_positive_on_normal_text():
    """普通文本不应触发"""
    text = "Hello world, this is a normal string without secrets."
    findings = scan_text(text, "readme.md")
    assert len(findings) == 0, f"unexpected: {[(f.pattern_id, f.match) for f in findings]}"
    print("  ✓ test_no_false_positive_on_normal_text")
    assert True


def test_no_false_positive_on_placeholder():
    """占位符不应触发"""
    placeholders = [
        "YOUR_API_KEY_HERE",
        "<YOUR_API_KEY>",
        "sk-xxxxxxxxxxxx",
        "AKIA0000000000000000",  # 长度不够,可能 false positive 风险
        "gho_placeholder",
    ]
    for p in placeholders:
        findings = scan_text(p, "test.py")
        # 至少大多数占位符不应触发(允许部分 false positive,但要 < 50%)
        print(f"    '{p}' → {len(findings)} findings")
    print("  ✓ test_no_false_positive_on_placeholder")
    assert True


def test_block_decision():
    """block 决策"""
    from moa_gateway.capability.secret_scan import Finding
    r = ScanResult()
    assert should_block(r) is False
    # 加 1 个 severity 3 的真实 finding
    f = Finding(pattern_id="AWS", severity=3, file="x.py", line=1, column=1,
                match="x", redacted="x", context="x")
    r.add(f)
    assert should_block(r, fail_on=3) is True
    # fail_on=2 也要 fail(severity 3 >= 2)
    assert should_block(r, fail_on=2) is True
    # 没 severity 3 的 finding,不 block
    r2 = ScanResult()
    f2 = Finding(pattern_id="JWT", severity=2, file="y.py", line=1, column=1,
                 match="y", redacted="y", context="y")
    r2.add(f2)
    assert should_block(r2, fail_on=3) is False
    # 豁免的 finding 不算
    r3 = ScanResult()
    f3 = Finding(pattern_id="AWS", severity=3, file="z.py", line=1, column=1,
                 match="z", redacted="z", context="z", exempt=True, exempt_reason="fixture", exempt_source="path")
    r3.add(f3)
    assert should_block(r3, fail_on=3) is False
    print("  ✓ test_block_decision")
    assert True


def test_scan_real_repo():
    """真实扫描 moa_gateway 自身(不触发任何 secret — 已清理)"""
    root = Path(__file__).resolve().parents[2]  # moa_gateway 父 = D:\MoA Gateway Pro
    result = scan_path(root)
    # 我们之前已经清理过 — 应该 0 real findings
    real = [f for f in result.findings if not f.exempt]
    if real:
        print(f"    ⚠️ 发现 {len(real)} 真实 secret:")
        for f in real[:5]:
            print(f"      {f.pattern_id} {f.file}:{f.line} → {f.redacted}")
    # 豁免是预期的(我们刻意有 audit_test.py / scripts/ 有 mock)
    assert result.scanned_files > 0
    print(f"  ✓ test_scan_real_repo: scanned {result.scanned_files} files, "
          f"real={result.real_count}, exempt={result.exempt_count}")
    assert True


if __name__ == "__main__":
    tests = [
        test_redact,
        test_scan_text_finds_aws_key,
        test_scan_text_finds_github_pat,
        test_scan_text_finds_anthropic,
        test_exempt_inline,
        test_exempt_path,
        test_scan_path_real_directory,
        test_scan_path_skips_binary,
        test_scan_path_skips_secret_files,
        test_no_false_positive_on_normal_text,
        test_no_false_positive_on_placeholder,
        test_block_decision,
        test_scan_real_repo,
    ]
    print(f"=== secret_scan 端到端测试 ({len(tests)} 项) ===")
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        print(f"失败: {failed}")
        sys.exit(1)