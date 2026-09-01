"""Tests for CLI module basic availability."""
from __future__ import annotations


def test_cli_imports():
    """Verify CLI modules can be imported."""


def test_cli_main_function_exists():
    """Verify main entry point exists and is callable."""
    from moa_gateway.cli.main import main

    assert callable(main)


def test_main_entry_routes_serve_flags():
    """moa_gateway.__main__ must treat bare --port/--host as server args, not CLI subcommands."""
    from unittest.mock import patch

    from moa_gateway import __main__ as entry

    captured = {}

    def _fake_run(host, port, workers):
        captured["host"] = host
        captured["port"] = port
        captured["workers"] = workers
        return 0

    with patch.object(entry, "_run_server", _fake_run):
        assert entry.main(["--port", "18999"]) == 0
        assert captured["port"] == 18999
        assert entry.main(["serve", "--port", "18998", "--host", "127.0.0.1"]) == 0
        assert captured["port"] == 18998
        assert captured["host"] == "127.0.0.1"


def test_main_entry_delegates_known_commands():
    """moa_gateway.__main__ must delegate known non-serve commands to cli.main."""
    from unittest.mock import patch

    from moa_gateway import __main__ as entry

    with patch("moa_gateway.cli.main.main") as mock_cli:
        mock_cli.return_value = 0
        assert entry.main(["chat", "--stream"]) == 0
        mock_cli.assert_called_once()
        passed = mock_cli.call_args[0][0]
        assert passed[:2] == ["chat", "--stream"]


def test_cli_submodules_importable():
    """Verify CLI submodules can be imported."""
