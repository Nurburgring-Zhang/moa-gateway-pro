"""Multi-source skill discovery with priority eviction.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/skill_loader.rb`` — the ``LOCATIONS`` priority chain
  (default < extension < global < project < brand) with ``register_skill``
  duplicate eviction by priority, the two-level directory layout (a skill dir
  holding SKILL.md directly, or a category dir containing skill subdirs),
  ``create_skill`` (slug validation + write to disk) and ``delete_skill``.

Mapping onto moa_gateway_pro v4.1.0 sources (ascending priority):
    bundled packs  (moa_gateway/skillhub/packs/)   priority 0
    extra dirs     (settings.skillhub.extra_dirs)  priority 1
    user skills    (<DATA_DIR>/skills)             priority 2
Higher-priority sources evict lower ones for the same skill name, exactly like
OpenClacky's later locations overriding earlier ones.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from .errors import SkillNotFoundError, SkillProtectedError, SkillValidationError
from .loader import build_skill_content, is_valid_slug, load_skill_file, slugify
from .models import Skill

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"

#: (source label, priority) pairs in ascending precedence.
SOURCE_BUNDLED = ("bundled", 0)
SOURCE_EXTRA = ("extra", 1)
SOURCE_USER = ("user", 2)


def bundled_packs_dir() -> Path:
    """The read-only skill packs shipped inside the package."""
    return Path(__file__).resolve().parent / "packs"


def default_user_skills_dir() -> Path:
    """User-created skills live under the gateway DATA_DIR.

    Resolved lazily so tests that patch ``moa_gateway.config.DATA_DIR`` are
    honored at call time.
    """
    from .. import config as _cfg

    return Path(_cfg.DATA_DIR) / "skills"


class SkillRegistry:
    """Discovers, loads and manages skills from all configured sources."""

    def __init__(
        self,
        extra_dirs: list[str] | None = None,
        user_dir: Path | None = None,
    ):
        if extra_dirs is None:
            from ..config import get_settings

            extra_dirs = list(get_settings().skillhub.extra_dirs)
        self._extra_dirs = [Path(d) for d in extra_dirs]
        self._user_dir = user_dir
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    # ---------- discovery ----------

    @property
    def user_dir(self) -> Path:
        return self._user_dir or default_user_skills_dir()

    def sources(self) -> list[tuple[Path, str, int]]:
        """(dir, source label, priority) in ascending priority order."""
        out: list[tuple[Path, str, int]] = [(bundled_packs_dir(), *SOURCE_BUNDLED)]
        for d in self._extra_dirs:
            out.append((d, *SOURCE_EXTRA))
        out.append((self.user_dir, *SOURCE_USER))
        return out

    def load_all(self, force: bool = False) -> dict[str, Skill]:
        """Scan every source; later (higher-priority) wins on name collision."""
        if self._loaded and not force:
            return self._skills
        found: dict[str, Skill] = {}
        for base, source, priority in self.sources():
            if not base.is_dir():
                continue
            for path in _iter_skill_files(base):
                skill = load_skill_file(path, source, priority)
                if skill is None:
                    continue
                prev = found.get(skill.name)
                if prev is not None and prev.priority >= skill.priority:
                    logger.info(
                        "skillhub: %s (source=%s) keeps %s over source=%s",
                        skill.name, prev.source, prev.dir_path, source,
                    )
                    continue
                if prev is not None:
                    logger.info(
                        "skillhub: %s skill %s overrides %s source",
                        source, skill.name, prev.source,
                    )
                found[skill.name] = skill
        self._skills = found
        self._loaded = True
        logger.info("skillhub: discovered %d skills", len(found))
        return found

    def list_skills(self) -> list[Skill]:
        return sorted(self.load_all().values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return self.load_all().get(name)

    def require(self, name: str) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise SkillNotFoundError(f"skill '{name}' not found")
        return skill

    # ---------- write operations (user source only) ----------

    def save_skill(
        self,
        name: str,
        meta: dict[str, Any],
        body: str,
        overwrite: bool = True,
    ) -> Path:
        """Create/replace a user skill on disk and refresh the registry."""
        if not is_valid_slug(name):
            raise SkillValidationError(
                f"invalid skill name {name!r}: must match ^[a-z0-9][a-z0-9-]*$"
            )
        if not body or not body.strip():
            raise SkillValidationError("skill content must not be empty")
        target_dir = self.user_dir / name
        target = target_dir / SKILL_FILENAME
        if target.exists() and not overwrite:
            raise SkillValidationError(f"skill '{name}' already exists")
        meta = dict(meta)
        meta["name"] = name
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(build_skill_content(meta, body), encoding="utf-8")
        logger.info("skillhub: wrote user skill %s -> %s", name, target)
        self.load_all(force=True)
        return target

    def delete_skill(self, name: str) -> str:
        """Delete a user-created skill directory. Bundled/extra are protected."""
        skill = self.get(name)
        if skill is None:
            raise SkillNotFoundError(f"skill '{name}' not found")
        if skill.source != "user":
            raise SkillProtectedError(
                f"skill '{name}' comes from the read-only '{skill.source}' source"
            )
        dir_path = Path(skill.dir_path)
        if not dir_path.is_dir():
            raise SkillNotFoundError(f"skill directory missing for '{name}'")
        shutil.rmtree(dir_path)
        logger.info("skillhub: deleted user skill %s (%s)", name, dir_path)
        self.load_all(force=True)
        return str(dir_path)


def _iter_skill_files(base: Path):
    """Yield SKILL.md paths under ``base`` (OpenClacky two-level layout).

    Level 1: ``base/<skill>/SKILL.md`` — a skill directory.
    Level 2: ``base/<category>/<skill>/SKILL.md`` — category grouping.
    """
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError as e:
        logger.warning("skillhub: cannot list %s: %s", base, e)
        return
    for entry in entries:
        direct = entry / SKILL_FILENAME
        if direct.is_file():
            yield direct
            continue
        try:
            sub_entries = sorted(p for p in entry.iterdir() if p.is_dir())
        except OSError:
            continue
        for sub in sub_entries:
            nested = sub / SKILL_FILENAME
            if nested.is_file():
                yield nested


def slug_for_name_hint(name_hint: str | None, description: str) -> str:
    """Derive a valid slug from a user-supplied hint or the description text."""
    if name_hint:
        slug = slugify(name_hint)
        if slug:
            return slug
    # fall back to leading ascii-ish words of the description
    slug = slugify(description)[:48].strip("-")
    if slug:
        return slug
    import zlib

    return f"skill-{zlib.crc32(description.encode('utf-8')) % 10**6}"
