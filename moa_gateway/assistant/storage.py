"""Assistant API storage — JSON file-based persistence."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .models import Assistant, Message, Run, RunStep, Thread

logger = logging.getLogger(__name__)


class AssistantStorage:
    """File-based storage for Assistant API entities."""

    def __init__(self, data_dir: str = "data/assistants"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Sub-directories for each entity type
        (self.data_dir / "assistants").mkdir(exist_ok=True)
        (self.data_dir / "threads").mkdir(exist_ok=True)
        (self.data_dir / "messages").mkdir(exist_ok=True)
        (self.data_dir / "runs").mkdir(exist_ok=True)
        (self.data_dir / "steps").mkdir(exist_ok=True)

    # --- Assistants ---

    def save_assistant(self, assistant: Assistant) -> Assistant:
        path = self.data_dir / "assistants" / f"{assistant.id}.json"
        path.write_text(json.dumps(assistant.model_dump(), ensure_ascii=False), encoding="utf-8")
        return assistant

    def get_assistant(self, assistant_id: str) -> Assistant | None:
        path = self.data_dir / "assistants" / f"{assistant_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Assistant(**data)

    def delete_assistant(self, assistant_id: str) -> bool:
        path = self.data_dir / "assistants" / f"{assistant_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_assistants(self, limit: int = 20) -> list[Assistant]:
        results = []
        folder = self.data_dir / "assistants"
        for f in sorted(folder.glob("*.json"), reverse=True)[:limit]:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(Assistant(**data))
        return results

    # --- Threads ---

    def save_thread(self, thread: Thread) -> Thread:
        path = self.data_dir / "threads" / f"{thread.id}.json"
        path.write_text(json.dumps(thread.model_dump(), ensure_ascii=False), encoding="utf-8")
        return thread

    def get_thread(self, thread_id: str) -> Thread | None:
        path = self.data_dir / "threads" / f"{thread_id}.json"
        if not path.exists():
            return None
        return Thread(**json.loads(path.read_text(encoding="utf-8")))

    def delete_thread(self, thread_id: str) -> bool:
        path = self.data_dir / "threads" / f"{thread_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- Messages ---

    def save_message(self, message: Message) -> Message:
        folder = self.data_dir / "messages" / message.thread_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{message.id}.json"
        path.write_text(json.dumps(message.model_dump(), ensure_ascii=False), encoding="utf-8")
        return message

    def list_messages(self, thread_id: str, limit: int = 100, order: str = "desc") -> list[Message]:
        folder = self.data_dir / "messages" / thread_id
        if not folder.exists():
            return []
        results = []
        for f in folder.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(Message(**data))
        results.sort(key=lambda m: m.created_at, reverse=(order == "desc"))
        return results[:limit]

    # --- Runs ---

    def save_run(self, run: Run) -> Run:
        folder = self.data_dir / "runs" / run.thread_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{run.id}.json"
        path.write_text(json.dumps(run.model_dump(), ensure_ascii=False), encoding="utf-8")
        return run

    def get_run(self, run_id: str) -> Run | None:
        # Search across thread sub-directories
        runs_dir = self.data_dir / "runs"
        for thread_dir in runs_dir.iterdir():
            if thread_dir.is_dir():
                path = thread_dir / f"{run_id}.json"
                if path.exists():
                    return Run(**json.loads(path.read_text(encoding="utf-8")))
        return None

    def list_runs(self, thread_id: str, limit: int = 20) -> list[Run]:
        folder = self.data_dir / "runs" / thread_id
        if not folder.exists():
            return []
        results = []
        for f in folder.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(Run(**data))
        results.sort(key=lambda r: r.created_at, reverse=True)
        return results[:limit]

    def iter_all_runs(self):
        """Yield every persisted run across all threads (D12 zombie sweep)."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return
        for thread_dir in runs_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            for f in thread_dir.glob("*.json"):
                try:
                    yield Run(**json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, ValueError, OSError) as exc:
                    logger.warning("skipping unreadable run file %s: %s", f, exc)

    def cleanup_stale_runs(self) -> int:
        """Fail runs left in queued/in_progress by a previous process (D12).

        Called once at startup: after a restart no background task is still
        executing those runs, so leaving them 'in_progress' forever would
        block the 409 active-run guard on their threads. Returns the number
        of runs marked failed.
        """
        stale = 0
        for run in self.iter_all_runs():
            if run.status in ("queued", "in_progress"):
                run.status = "failed"
                run.failed_at = int(time.time())
                run.last_error = {
                    "code": "server_error",
                    "message": "run interrupted by gateway restart",
                }
                self.save_run(run)
                stale += 1
        return stale

    # --- Run Steps ---

    def save_step(self, step: RunStep) -> RunStep:
        folder = self.data_dir / "steps" / step.run_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{step.id}.json"
        path.write_text(json.dumps(step.model_dump(), ensure_ascii=False), encoding="utf-8")
        return step

    def list_steps(self, run_id: str) -> list[RunStep]:
        folder = self.data_dir / "steps" / run_id
        if not folder.exists():
            return []
        results = []
        for f in folder.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(RunStep(**data))
        results.sort(key=lambda s: s.created_at)
        return results


# Global singleton
_storage: AssistantStorage | None = None


def get_storage() -> AssistantStorage:
    global _storage
    if _storage is None:
        _storage = AssistantStorage()
    return _storage
