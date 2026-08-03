"""Tests for CLI module basic availability."""
from __future__ import annotations

import pytest


def test_cli_imports():
    """Verify CLI modules can be imported."""
    from moa_gateway.cli import main


def test_cli_main_function_exists():
    """Verify main entry point exists and is callable."""
    from moa_gateway.cli.main import main

    assert callable(main)


def test_cli_submodules_importable():
    """Verify CLI submodules can be imported."""
    from moa_gateway.cli import ai_suggest
    from moa_gateway.cli import chat_repl
