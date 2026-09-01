"""Skill evolution hooks — usage accounting, reflection and auto-creation.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/agent/skill_evolution.rb`` — evolution loop orchestration;
- ``lib/clacky/agent/skill_reflector.rb`` — ``MIN_SKILL_ITERATIONS = 5`` and
  the reflect prompt (model reviews the skill after N uses and proposes
  improvements);
- ``lib/clacky/agent/skill_auto_creator.rb`` — ``DEFAULT_AUTO_CREATE_THRESHOLD
  = 12`` and its decision criteria (when an ad-hoc task repeats often enough,
  promote it into a real skill).

Thresholds are not hardcoded here: they come from ``SkillHubConfig``
(``evolution_min_iterations`` default 5, ``auto_create_min_iterations``
default 12 — the exact OpenClacky values).

Persistence uses three tables created with ``CREATE TABLE IF NOT EXISTS`` via
the shared Storage connection; ``storage.py`` itself is untouched.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .models import Skill

logger = logging.getLogger(__name__)

_TABLE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS skill_usage_stats (
        name TEXT PRIMARY KEY,
        invocations INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0,
        last_task TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS skill_evolution_suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        iteration INTEGER NOT NULL DEFAULT 0,
        suggestion TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS skill_adhoc_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ses_skill_name ON skill_evolution_suggestions(skill_name)",
    "CREATE INDEX IF NOT EXISTS idx_ses_kind ON skill_evolution_suggestions(kind)",
)


class SkillEvolutionStore:
    """SQLite persistence for usage stats, suggestions and ad-hoc tasks."""

    def __init__(self):
        self._schema_ready = False

    @property
    def _storage(self):
        from ..storage import get_storage

        return get_storage()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._storage.conn() as c:
            for stmt in _TABLE_STATEMENTS:
                c.execute(stmt)
        self._schema_ready = True

    # ---------- usage stats ----------

    def record_invocation(self, name: str, task: str, ok: bool = True) -> int:
        """Upsert usage counters; returns the new invocation count."""
        self._ensure_schema()
        now = time.time()
        with self._storage.conn() as c:
            row = c.execute(
                "SELECT invocations FROM skill_usage_stats WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO skill_usage_stats "
                    "(name, invocations, successes, failures, last_task, updated_at) "
                    "VALUES (?, 1, ?, ?, ?, ?)",
                    (name, 1 if ok else 0, 0 if ok else 1, task[:500], now),
                )
                return 1
            c.execute(
                "UPDATE skill_usage_stats SET invocations = invocations + 1, "
                "successes = successes + ?, failures = failures + ?, "
                "last_task = ?, updated_at = ? WHERE name = ?",
                (1 if ok else 0, 0 if ok else 1, task[:500], now, name),
            )
            return int(row["invocations"]) + 1

    def stats(self, name: str) -> dict[str, Any]:
        self._ensure_schema()
        with self._storage.conn() as c:
            row = c.execute(
                "SELECT * FROM skill_usage_stats WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return {
                "name": name, "invocations": 0, "successes": 0,
                "failures": 0, "last_task": "", "updated_at": None,
            }
        return {
            "name": row["name"],
            "invocations": int(row["invocations"]),
            "successes": int(row["successes"]),
            "failures": int(row["failures"]),
            "last_task": row["last_task"],
            "updated_at": row["updated_at"],
        }

    def top_skills(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._storage.conn() as c:
            rows = c.execute(
                "SELECT * FROM skill_usage_stats ORDER BY invocations DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- suggestions ----------

    def add_suggestion(
        self,
        skill_name: str,
        kind: str,
        iteration: int,
        suggestion: str,
        status: str = "open",
    ) -> int:
        self._ensure_schema()
        with self._storage.conn() as c:
            cur = c.execute(
                "INSERT INTO skill_evolution_suggestions "
                "(skill_name, kind, iteration, suggestion, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (skill_name, kind, iteration, suggestion, status, time.time()),
            )
            return int(cur.lastrowid or 0)

    def has_suggestion_at(self, skill_name: str, kind: str, iteration: int) -> bool:
        self._ensure_schema()
        with self._storage.conn() as c:
            row = c.execute(
                "SELECT id FROM skill_evolution_suggestions "
                "WHERE skill_name = ? AND kind = ? AND iteration = ? LIMIT 1",
                (skill_name, kind, iteration),
            ).fetchone()
        return row is not None

    def list_suggestions(
        self, skill_name: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        sql = "SELECT * FROM skill_evolution_suggestions"
        clauses, params = [], []
        if skill_name is not None:
            clauses.append("skill_name = ?")
            params.append(skill_name)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        with self._storage.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- ad-hoc tasks (auto-create signal) ----------

    def record_adhoc_task(self, task: str) -> None:
        self._ensure_schema()
        with self._storage.conn() as c:
            c.execute(
                "INSERT INTO skill_adhoc_tasks (task, created_at) VALUES (?, ?)",
                (task[:1000], time.time()),
            )

    def adhoc_tasks(self, limit: int = 500) -> list[str]:
        self._ensure_schema()
        with self._storage.conn() as c:
            rows = c.execute(
                "SELECT task FROM skill_adhoc_tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["task"] for r in rows]


def heuristic_review(skill: Skill, stats: dict[str, Any]) -> str:
    """Deterministic structural review — real analysis of the skill content.

    Used directly when no model endpoint is available, and as grounding
    context for the LLM reflection prompt otherwise.
    """
    issues: list[str] = []
    desc = skill.description or ""
    body = skill.content or ""
    body_l = body.lower()

    if len(desc) < 40:
        issues.append(
            "description is thin (<40 chars); rewrite it as one sentence that "
            "states the input, the transformation and the delivered outcome"
        )
    if not skill.triggers:
        issues.append(
            "no triggers defined; add 3-6 keywords users would actually type "
            "so fuzzy search can route to this skill"
        )
    if not any(marker in body for marker in ("1.", "## Workflow", "## 步骤", "Steps:")):
        issues.append(
            "the body lacks an explicit numbered workflow; add concrete ordered "
            "steps so execution is reproducible"
        )
    if not any(k in body_l for k in ("constraint", "约束", "rules", "规则")):
        issues.append("no constraints section; spell out what the skill must never do")
    if not any(k in body_l for k in ("example", "示例", "e.g.", "for instance")):
        issues.append("no worked example; add one input->output example to anchor style")
    if not skill.argument_hint:
        issues.append("set argument-hint so callers know the expected input shape")

    invocations = stats.get("invocations", 0)
    failures = stats.get("failures", 0)
    header = (
        f"After {invocations} invocations ({failures} failed) of skill "
        f"'{skill.name}': "
    )
    if issues:
        return header + "; ".join(f"({i + 1}) {t}" for i, t in enumerate(issues))
    return (
        header
        + "structure is solid (description, triggers, workflow, constraints all "
        "present); next improvement should come from real usage: tighten the "
        "output format against the tasks users actually send"
    )


class SkillEvolutionManager:
    """Evolution orchestration driven by SkillHubConfig thresholds."""

    def __init__(self, store: SkillEvolutionStore | None = None):
        self.store = store or SkillEvolutionStore()

    @staticmethod
    def _cfg():
        from ..config import get_settings

        return get_settings().skillhub

    async def on_invocation(
        self, skill: Skill, task: str, ok: bool = True
    ) -> dict[str, Any] | None:
        """Record a usage, then run the milestone check (see check_milestone)."""
        self.store.record_invocation(skill.name, task, ok=ok)
        return await self.check_milestone(skill)

    async def check_milestone(self, skill: Skill) -> dict[str, Any] | None:
        """At each ``evolution_min_iterations`` milestone emit one real
        improvement-suggestion record (deduplicated per iteration count).
        Call after usage has been recorded (no double counting)."""
        cfg = self._cfg()
        if not cfg.evolution_enabled:
            return None
        count = self.store.stats(skill.name)["invocations"]
        threshold = max(1, cfg.evolution_min_iterations)
        if count <= 0 or count % threshold != 0:
            return None
        if self.store.has_suggestion_at(skill.name, "reflect", count):
            return None
        suggestion = await self.reflect(skill)
        sid = self.store.add_suggestion(skill.name, "reflect", count, suggestion)
        logger.info(
            "skillhub: evolution reflect for '%s' at iteration %d (suggestion #%d)",
            skill.name, count, sid,
        )
        return {
            "type": "reflect",
            "skill": skill.name,
            "iteration": count,
            "suggestion_id": sid,
            "suggestion": suggestion,
        }

    async def reflect(self, skill: Skill) -> str:
        """Improvement review: LLM reflection when the pipeline is available,
        deterministic structural analysis otherwise. Both are real reviews."""
        stats = self.store.stats(skill.name)
        grounding = heuristic_review(skill, stats)
        try:
            from .invoker import call_model_pipeline

            prompt = (
                "You are the skill-evolution reflector of an agent skill hub.\n"
                "Review the skill below after repeated real usage and propose "
                "concrete improvements to its SKILL.md (prompt quality, missing "
                "steps, ambiguous output format, missing edge cases).\n\n"
                f"Usage stats: {stats['invocations']} invocations, "
                f"{stats['failures']} failures. Last task: {stats['last_task']!r}\n\n"
                f"Skill name: {skill.name}\nDescription: {skill.description}\n"
                f"SKILL.md body:\n{skill.content[:6000]}\n\n"
                f"Automated structural analysis already found: {grounding}\n\n"
                "Answer with a prioritized list of 2-5 specific, actionable "
                "improvements. No generic advice."
            )
            resp, _ep = await call_model_pipeline(
                [
                    {"role": "system", "content": "You give precise, evidence-based reviews."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
            )
            text = (resp.content or "").strip()
            if len(text) >= 20:
                return text
        except Exception as e:
            logger.info("skillhub: LLM reflection unavailable (%s), using heuristic review", e)
        return grounding

    async def maybe_auto_create(self, task: str) -> dict[str, Any] | None:
        """Track ad-hoc (skill-less) tasks; when a similar cluster reaches
        ``auto_create_min_iterations`` (OpenClacky threshold 12), really create
        a skill from the repeated task and record an ``auto_create`` suggestion.

        Returns creation info dict or None while under threshold.
        """
        if not task or not task.strip():
            return None
        cfg = self._cfg()
        if not cfg.evolution_enabled:
            return None
        threshold = max(1, cfg.auto_create_min_iterations)
        self.store.record_adhoc_task(task.strip())

        from .search import _ratio

        cluster = [
            t for t in self.store.adhoc_tasks()
            if _ratio(t, task) >= 0.6 or t.strip().lower() == task.strip().lower()
        ]
        if len(cluster) < threshold:
            return None

        # OpenClacky auto-creator decision criteria (repetitiveness proven by
        # the cluster; remaining criteria checked before creation):
        if len(task.strip()) < 8:
            return None  # too trivial to deserve a skill

        from .creator import create_skill_from_description

        description = (
            f"Automatically created skill for a repeatedly requested task "
            f"({len(cluster)} similar ad-hoc requests). Task: {task.strip()}"
        )
        created = await create_skill_from_description(description=description)
        slug = created["skill"]["name"]
        suggestion = (
            f"Auto-created from {len(cluster)} similar ad-hoc tasks "
            f"(threshold {threshold}). Criteria met: high repetition, "
            f"non-trivial task length, stable phrasing cluster, clear single "
            f"capability, generation succeeded. Review the generated steps and "
            f"add domain examples."
        )
        self.store.add_suggestion(slug, "auto_create", len(cluster), suggestion)
        logger.info(
            "skillhub: auto-created skill '%s' from %d similar ad-hoc tasks",
            slug, len(cluster),
        )
        return {
            "type": "auto_create",
            "skill": slug,
            "cluster_size": len(cluster),
            "threshold": threshold,
            "path": created["path"],
            "generated_by": created["generated_by"],
        }
