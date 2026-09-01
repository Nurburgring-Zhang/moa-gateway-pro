"""SkillHub exception hierarchy.

All SkillHub (M7) errors carry an HTTP status code so the route layer can map
domain failures to honest HTTP responses without string-sniffing.
"""

from __future__ import annotations


class SkillHubError(Exception):
    """Base class for all SkillHub domain errors."""

    status_code: int = 500

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class SkillNotFoundError(SkillHubError):
    """Requested skill does not exist in any discovery source."""

    status_code = 404


class SkillValidationError(SkillHubError):
    """Skill payload failed validation (bad slug, missing fields, bad YAML)."""

    status_code = 422


class SkillProtectedError(SkillHubError):
    """Attempt to modify/delete a read-only (bundled/extra) skill."""

    status_code = 403


class SkillInvokeError(SkillHubError):
    """invoke_skill failed at the model-pipeline level."""

    status_code = 502
