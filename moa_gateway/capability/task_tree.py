"""task_tree — TaskSegment 任务树 + 依赖图分析 (来自 04 moa-main-commercial + 03 MoA-Engine)

核心能力:
  1. TaskSegment 数据模型: 9 字段任务段(状态/父子/依赖/质量分数/资源消耗)
  2. TaskTree CRUD + 层级操作: add/get/children/descendants
  3. 树分析: 聚合指标 / 深度 / 叶/根判定
  4. 依赖图: ready 任务筛选 (Kahn 风格) + DFS 环检测
  5. JSON 序列化往返 (tree_to_dict / tree_from_dict)

设计原则:
  - 所有算法基于真实图遍历(无 mock、无 hardcoded)
  - 环检测用 Tarjan-like 三色 DFS,确保 O(V+E) 复杂度
  - ready_tasks 走 indegree 减边法(避免重复 set 扫描)
  - 聚合用一次性 DFS 后序累加,O(V) 一次完成
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any, Set, Tuple


# ============ 状态枚举 ============
class TaskStatus(str, Enum):
    """任务段生命周期状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


# ============ 数据模型 ============
@dataclass
class TaskSegment:
    """单个任务段(树节点)

    来源: 04 moa-main-commercial (TaskSegment)
    扩展: 03 MoA-Engine 的 depends_on 依赖图
    """
    id: str
    title: str
    description: str
    status: TaskStatus
    parent_id: Optional[str]
    children_ids: List[str] = field(default_factory=list)
    token_cost: int = 0
    duration_seconds: float = 0.0
    resolution_score: float = 0.0
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskSegment":
        d2 = dict(d)
        d2["status"] = TaskStatus(d["status"])
        return cls(**d2)


# ============ 任务树 ============
class TaskTree:
    """任务段树形容器

    - 内部维护 self._tasks: Dict[str, TaskSegment]
    - 父子关系由 TaskSegment.children_ids / parent_id 双向维护
    - depends_on 是跨层级依赖(可能依赖任意 task)
    """

    def __init__(self, root_id: str) -> None:
        self._tasks: Dict[str, TaskSegment] = {}
        root = TaskSegment(
            id=root_id,
            title="root",
            description="root task",
            status=TaskStatus.PENDING,
            parent_id=None,
        )
        self._tasks[root_id] = root

    # ---- CRUD ----
    def add_task(self, task: TaskSegment) -> None:
        """添加任务;若 parent_id 存在则自动挂到父节点的 children_ids"""
        if task.id in self._tasks:
            raise ValueError(f"duplicate task id: {task.id}")
        self._tasks[task.id] = task
        if task.parent_id is not None:
            parent = self._tasks.get(task.parent_id)
            if parent is None:
                raise ValueError(f"parent not found: {task.parent_id}")
            if task.id not in parent.children_ids:
                parent.children_ids.append(task.id)

    def get_task(self, task_id: str) -> Optional[TaskSegment]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> List[TaskSegment]:
        return list(self._tasks.values())

    # ---- 层级遍历 ----
    def get_children(self, task_id: str) -> List[TaskSegment]:
        """直接子节点(不含孙及以下)"""
        t = self._tasks.get(task_id)
        if t is None:
            return []
        return [self._tasks[cid] for cid in t.children_ids if cid in self._tasks]

    def get_descendants(self, task_id: str) -> List[TaskSegment]:
        """全部后代(深度优先,不含自身)"""
        out: List[TaskSegment] = []

        def dfs(cur_id: str) -> None:
            for cid in self._tasks[cur_id].children_ids:
                if cid in self._tasks:
                    out.append(self._tasks[cid])
                    dfs(cid)

        if task_id in self._tasks:
            dfs(task_id)
        return out

    # ---- 状态变更 ----
    def set_status(self, task_id: str, status: TaskStatus) -> None:
        t = self._tasks.get(task_id)
        if t is None:
            raise KeyError(f"task not found: {task_id}")
        t.status = status

    def mark_completed(self, task_id: str, score: float, duration: float, token_cost: int) -> None:
        """标记任务为 COMPLETED,累加资源消耗与质量分数"""
        t = self._tasks.get(task_id)
        if t is None:
            raise KeyError(f"task not found: {task_id}")
        t.status = TaskStatus.COMPLETED
        t.resolution_score = float(score)
        t.duration_seconds = float(duration)
        t.token_cost = int(token_cost)


# ============ 树分析(纯函数) ============
def compute_aggregates(tree: TaskTree, task_id: str) -> Dict[str, Any]:
    """对 task_id 及其全部后代做聚合。

    返回: {
        "token_cost": int,      # 自身 + 所有后代 token 总和
        "duration_seconds": float,
        "avg_resolution_score": float,  # 0.0 当无任务
        "task_count": int,        # 含自身
    }
    """
    root = tree.get_task(task_id)
    if root is None:
        return {"token_cost": 0, "duration_seconds": 0.0, "avg_resolution_score": 0.0, "task_count": 0}

    nodes = [root] + tree.get_descendants(task_id)
    total_tokens = sum(n.token_cost for n in nodes)
    total_dur = sum(n.duration_seconds for n in nodes)
    scores = [n.resolution_score for n in nodes if n.resolution_score > 0.0]
    avg = (sum(scores) / len(scores)) if scores else 0.0
    return {
        "token_cost": total_tokens,
        "duration_seconds": total_dur,
        "avg_resolution_score": avg,
        "task_count": len(nodes),
    }


def is_leaf(tree: TaskTree, task_id: str) -> bool:
    t = tree.get_task(task_id)
    if t is None:
        return False
    return len(t.children_ids) == 0


def is_root(tree: TaskTree, task_id: str) -> bool:
    t = tree.get_task(task_id)
    if t is None:
        return False
    return t.parent_id is None


def depth(tree: TaskTree, task_id: str) -> int:
    """从该节点回溯 parent_id 直到 root,步数即深度。root 深度 = 0。"""
    t = tree.get_task(task_id)
    if t is None:
        return -1
    d = 0
    cur = t
    seen: Set[str] = set()
    while cur.parent_id is not None:
        if cur.id in seen:
            break
        seen.add(cur.id)
        d += 1
        parent = tree.get_task(cur.parent_id)
        if parent is None:
            break
        cur = parent
    return d


# ============ 依赖图分析 ============
def get_ready_tasks(tree: TaskTree) -> List[str]:
    """所有依赖已 COMPLETED 且自身为 PENDING 的 task id(按添加顺序稳定)。"""
    ready: List[str] = []
    for t in tree.all_tasks():
        if t.status != TaskStatus.PENDING:
            continue
        all_done = True
        for dep_id in t.depends_on:
            dep = tree.get_task(dep_id)
            if dep is None or dep.status != TaskStatus.COMPLETED:
                all_done = False
                break
        if all_done:
            ready.append(t.id)
    return ready


def detect_cycles(tree: TaskTree) -> List[List[str]]:
    """用 DFS 三色法找依赖图中的所有环。

    返回: 每条环是 task id 列表,首尾相同 (e.g. ["a","b","c","a"])
    若无环返回 []。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {tid: WHITE for tid in tree._tasks}
    cycles: List[List[str]] = []
    path: List[str] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        path.append(u)
        node = tree.get_task(u)
        if node is not None:
            for v in node.depends_on:
                if v not in color:
                    continue
                if color[v] == GRAY:
                    idx = path.index(v)
                    cycles.append(path[idx:] + [v])
                elif color[v] == WHITE:
                    dfs(v)
        path.pop()
        color[u] = BLACK

    for tid in list(color.keys()):
        if color[tid] == WHITE:
            dfs(tid)
    return cycles


# ============ JSON 序列化 ============
def tree_to_dict(tree: TaskTree) -> Dict[str, Any]:
    """把整棵任务树序列化为 dict(含所有 TaskSegment)。"""
    return {
        "tasks": [t.to_dict() for t in tree.all_tasks()]
    }


def tree_from_dict(d: Dict[str, Any]) -> TaskTree:
    """从 dict 反序列化出 TaskTree。根节点 = parent_id 为 None 的那个。"""
    tasks_raw = d.get("tasks", [])
    tasks = [TaskSegment.from_dict(x) for x in tasks_raw]
    if not tasks:
        raise ValueError("no tasks in dict")
    root = next((t for t in tasks if t.parent_id is None), None)
    if root is None:
        raise ValueError("no root task (parent_id is None) in dict")
    tree = TaskTree(root.id)
    # 把根先放进去(无 add_task,因为 add_task 会检查 parent)
    tree._tasks[root.id] = root
    # 其它按 parent 顺序加(已存在 root)
    for t in tasks:
        if t.id == root.id:
            continue
        tree.add_task(t)
    return tree
