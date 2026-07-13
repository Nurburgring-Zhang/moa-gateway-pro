"""A-21 Artifact Schema 统一结构 + A-50 Tmux 面板编排 (CG mode)

- A-21: 共享 schema for agents / skills / connectors / actions / experiment-plans
  (来源 04 moa-main-commercial — 商业版统一的 artifact 元数据规范)
- A-50: Tmux 面板编排 (Computer-Generated mode) — 限可见面板 + 敏感环境变量安全门
  (来源 06 moai-adk-multiagent — CG mode 限 N 个可见 pane + 防 secret 泄漏)

真实实现,非 mock。SchemaRegistry 内存索引,TmuxOrchestrator 限面板数 + 敏感 argv 检测。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional


# ============ A-21: Artifact Type Enum ============

class ArtifactType(str, Enum):
    """5 类共享 artifact 类型"""
    AGENT = "agent"
    SKILL = "skill"
    CONNECTOR = "connector"
    ACTION = "action"
    EXPERIMENT_PLAN = "experiment_plan"


# 必填字段 (id / name / type / description)
REQUIRED_FIELDS: List[str] = ["id", "name", "type", "description"]


# ============ A-21: Artifact dataclass ============

@dataclass
class Artifact:
    """共享 artifact schema — agents / skills / connectors / actions / experiment-plans 通用"""
    id: str
    name: str
    type: ArtifactType
    description: str
    version: str = "1.0.0"
    schema_version: int = 1
    tags: List[str] = field(default_factory=list)
    inputs: Dict = field(default_factory=dict)
    outputs: Dict = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        d = asdict(self)
        # ArtifactType 序列化为 value
        if isinstance(d.get("type"), ArtifactType):
            d["type"] = d["type"].value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ============ A-21: SchemaRegistry ============

class SchemaRegistry:
    """Artifact 内存注册表 — register / get / list_by_type / validate"""

    def __init__(self) -> None:
        self._items: Dict[str, Artifact] = {}

    def register(self, artifact: Artifact) -> None:
        """注册一个 artifact (按 id 索引,后者覆盖前者)"""
        if not isinstance(artifact, Artifact):
            raise TypeError(f"expected Artifact, got {type(artifact).__name__}")
        self._items[artifact.id] = artifact

    def get(self, artifact_id: str) -> Optional[Artifact]:
        """按 id 获取;不存在返回 None"""
        return self._items.get(artifact_id)

    def list_by_type(self, type: ArtifactType) -> List[Artifact]:
        """按类型列出所有 artifact"""
        if not isinstance(type, ArtifactType):
            raise TypeError(f"expected ArtifactType, got {type(type).__name__}")
        return [a for a in self._items.values() if a.type == type]

    def all(self) -> List[Artifact]:
        """全部 artifact"""
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def validate(self, artifact: Artifact) -> List[str]:
        """校验必填字段,返回缺失字段名列表 (空 = 通过)"""
        missing: List[str] = []
        if not isinstance(artifact, Artifact):
            return list(REQUIRED_FIELDS)
        for f in REQUIRED_FIELDS:
            v = getattr(artifact, f, None)
            if v is None:
                missing.append(f)
                continue
            # str / ArtifactType 都不能为空
            if isinstance(v, str) and not v.strip():
                missing.append(f)
            elif isinstance(v, ArtifactType) and not v.value:
                missing.append(f)
        return missing

    def to_dict(self) -> Dict:
        return {
            "count": len(self._items),
            "by_type": {
                t.value: [a.id for a in self.list_by_type(t)]
                for t in ArtifactType
            },
            "items": {aid: a.to_dict() for aid, a in self._items.items()},
        }


# ============ A-50: TmuxPane dataclass ============

@dataclass
class TmuxPane:
    """一个 tmux 面板的描述 (CG mode)"""
    pane_id: str
    command: str
    cwd: str
    env_vars: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ A-50: 敏感 argv 检测 ============

# 触发 unsafe 的关键词 (大小写不敏感)
SENSITIVE_KEYWORDS: List[str] = [
    "password",
    "secret",
    "api_key",
    "apikey",
    "api-key",
    "token",
    "passwd",
    "private_key",
]

_SENSITIVE_RE = re.compile(
    r"(?i)(?:^|[\s=:\-/])(?:" + "|".join(re.escape(k) for k in SENSITIVE_KEYWORDS) + r")(?:$|[\s=:'\"])",
)


def _argv_contains_sensitive(argv_blob: str) -> bool:
    """检测一段字符串里是否含敏感关键词 (password / secret / api_key 等)
    用于 command / 自由文本 — 用 word boundary 防误伤 'passport' 等。
    """
    if not argv_blob:
        return False
    return bool(_SENSITIVE_RE.search(argv_blob))


def _key_contains_sensitive(key: str) -> bool:
    """检测 env var key 名是否含敏感关键词 (substr 大小写不敏感)
    例如 DB_PASSWORD / AWS_SECRET_KEY / GITHUB_TOKEN → True
    """
    if not key:
        return False
    k = key.lower()
    return any(s in k for s in SENSITIVE_KEYWORDS)


# ============ A-50: TmuxOrchestrator ============

class TmuxOrchestrator:
    """CG mode: 限制 max_visible 个面板同时可见,敏感 argv 安全门"""

    def __init__(self, max_visible: int = 3) -> None:
        if not isinstance(max_visible, int) or max_visible < 0:
            raise ValueError(f"max_visible must be non-negative int, got {max_visible!r}")
        self.max_visible = max_visible
        self._panes: List[TmuxPane] = []

    def add_pane(self, pane: TmuxPane) -> None:
        """追加一个面板"""
        if not isinstance(pane, TmuxPane):
            raise TypeError(f"expected TmuxPane, got {type(pane).__name__}")
        self._panes.append(pane)

    def layout(self) -> List[TmuxPane]:
        """返回当前可见面板 (前 max_visible 个)"""
        return list(self._panes[: self.max_visible])

    def overflow(self) -> List[TmuxPane]:
        """返回被截断的面板 (CG mode 隐藏部分)"""
        return list(self._panes[self.max_visible:])

    def sensitive_env_safe(self, pane: TmuxPane) -> bool:
        """检测面板 command + env_vars 是否含敏感关键词
        规则: command 字符串 OR 任意 env key (value 一律视为敏感) 中含 password/secret/api_key 等 → False
        """
        if not isinstance(pane, TmuxPane):
            raise TypeError(f"expected TmuxPane, got {type(pane).__name__}")
        # 1) command 检测
        if _argv_contains_sensitive(pane.command or ""):
            return False
        # 2) env key 检测 (key 名即视为风险 — 实际部署应改走 secret store)
        for k in (pane.env_vars or {}).keys():
            if _key_contains_sensitive(str(k)):
                return False
        return True

    def safe_layout(self) -> List[TmuxPane]:
        """layout() 的安全子集 — 仅返回 sensitive_env_safe 通过的面板"""
        return [p for p in self.layout() if self.sensitive_env_safe(p)]

    def __len__(self) -> int:
        return len(self._panes)

    def to_dict(self) -> Dict:
        return {
            "max_visible": self.max_visible,
            "total_panes": len(self._panes),
            "visible_panes": [p.to_dict() for p in self.layout()],
        }
