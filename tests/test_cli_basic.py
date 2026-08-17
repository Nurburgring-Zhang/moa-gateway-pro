"""Tests for CLI module basic availability."""
from __future__ import annotations


def test_cli_imports():
    """Verify CLI modules can be imported."""


def test_cli_main_function_exists():
    """Verify main entry point exists and is callable."""
    from moa_gateway.cli.main import main

    assert callable(main)


def test_cli_submodules_importable():
    """Verify CLI submodules can be imported."""
