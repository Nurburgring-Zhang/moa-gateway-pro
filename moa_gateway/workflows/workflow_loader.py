"""Workflow loader — load YAML workflows from the filesystem.

Supports loading individual workflow files or scanning a directory
for all ``.yaml`` / ``.yml`` workflow definitions.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .yaml_workflow import WorkflowYAML

logger = logging.getLogger(__name__)


class WorkflowLoader:
    """Load and manage YAML workflow definitions from the filesystem.

    The default workflow directory is ``moa_gateway/workflows/builtin/``
    but any directory can be specified.
    """

    def __init__(self, workflow_dir: str | Path | None = None) -> None:
        """Initialize the loader.

        Args:
            workflow_dir: Directory containing YAML workflow files.
                          If None, uses the builtin directory.
        """
        if workflow_dir is None:
            workflow_dir = Path(__file__).parent / "builtin"
        self.workflow_dir = Path(workflow_dir)

    def load_from_file(self, file_path: str | Path) -> WorkflowYAML:
        """Load a single workflow from a YAML file.

        Args:
            file_path: Path to the YAML file.

        Returns:
            WorkflowYAML instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the YAML is invalid.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {path}")

        content = path.read_text(encoding="utf-8")
        return WorkflowYAML(content)

    def load_from_dir(
        self, dir_path: str | Path | None = None
    ) -> list[WorkflowYAML]:
        """Load all workflow YAML files from a directory.

        Args:
            dir_path: Directory to scan. If None, uses the configured
                      workflow_dir.

        Returns:
            List of WorkflowYAML instances.
        """
        target = Path(dir_path) if dir_path else self.workflow_dir
        if not target.exists():
            return []

        workflows: list[WorkflowYAML] = []
        for ext in ("*.yaml", "*.yml"):
            for path in sorted(target.glob(ext)):
                try:
                    wf = self.load_from_file(path)
                    workflows.append(wf)
                    logger.debug("Loaded workflow '%s' from %s", wf.name, path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to load workflow from %s: %s", path, exc)

        return workflows

    def list_workflows(
        self, dir_path: str | Path | None = None
    ) -> list[dict[str, str]]:
        """List available workflows with metadata.

        Args:
            dir_path: Directory to scan. If None, uses workflow_dir.

        Returns:
            List of dicts with keys: name, description, version, file.
        """
        target = Path(dir_path) if dir_path else self.workflow_dir
        if not target.exists():
            return []

        result: list[dict[str, str]] = []
        for ext in ("*.yaml", "*.yml"):
            for path in sorted(target.glob(ext)):
                try:
                    wf = self.load_from_file(path)
                    result.append({
                        "name": wf.name,
                        "description": wf.description,
                        "version": wf.version,
                        "file": path.name,
                        "steps": str(len(wf.steps)),
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to list workflow %s: %s", path, exc)

        return result

    def get_workflow(
        self, name: str, dir_path: str | Path | None = None
    ) -> WorkflowYAML | None:
        """Find a workflow by name.

        Args:
            name: Workflow name (matches the 'name' field in YAML).
            dir_path: Directory to search. If None, uses workflow_dir.

        Returns:
            WorkflowYAML if found, None otherwise.
        """
        target = Path(dir_path) if dir_path else self.workflow_dir
        if not target.exists():
            return None

        for ext in ("*.yaml", "*.yml"):
            for path in sorted(target.glob(ext)):
                try:
                    wf = self.load_from_file(path)
                    if wf.name == name:
                        return wf
                except Exception:  # noqa: BLE001
                    continue

        return None

    def save_workflow(
        self,
        name: str,
        yaml_content: str,
        dir_path: str | Path | None = None,
    ) -> str:
        """Save a new workflow YAML file.

        Args:
            name: Workflow name (used for filename).
            yaml_content: Raw YAML content.
            dir_path: Target directory. If None, uses workflow_dir.

        Returns:
            Path to the saved file.

        Raises:
            ValueError: If the YAML content is invalid.
        """
        # Validate the YAML before saving
        WorkflowYAML(yaml_content)

        target = Path(dir_path) if dir_path else self.workflow_dir
        target.mkdir(parents=True, exist_ok=True)

        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in name
        )
        file_path = target / f"{safe_name}.yaml"
        file_path.write_text(yaml_content, encoding="utf-8")

        logger.info("Saved workflow '%s' to %s", name, file_path)
        return str(file_path)
