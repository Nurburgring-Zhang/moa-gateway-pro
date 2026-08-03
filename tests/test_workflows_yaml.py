"""Tests for YAML workflow engine."""
from __future__ import annotations

import pytest


def test_workflow_imports():
    """Verify workflow modules can be imported."""
    from moa_gateway.workflows import yaml_workflow
    from moa_gateway.workflows import workflow_loader


def test_workflow_yaml_class_exists():
    """Verify WorkflowYAML class is available."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    assert WorkflowYAML is not None


def test_workflow_loader_exists():
    """Verify WorkflowLoader class is available."""
    from moa_gateway.workflows.workflow_loader import WorkflowLoader

    assert WorkflowLoader is not None


def test_workflow_public_api():
    """Verify all public symbols are exported from workflows package."""
    from moa_gateway.workflows import WorkflowLoader, WorkflowStep, WorkflowYAML

    assert WorkflowLoader is not None
    assert WorkflowStep is not None
    assert WorkflowYAML is not None


def test_workflow_yaml_instantiation():
    """Verify WorkflowYAML can be instantiated with minimal YAML."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    minimal_yaml = """
name: test_workflow
steps:
  - id: step1
    action: echo
    inputs:
      message: "hello"
"""
    wf = WorkflowYAML(minimal_yaml)
    assert wf is not None


def test_workflow_step_dataclass():
    """Verify WorkflowStep is a usable dataclass/class."""
    from moa_gateway.workflows.yaml_workflow import WorkflowStep

    assert WorkflowStep is not None
    # Should be instantiable (check constructor signature exists)
    assert callable(WorkflowStep)
