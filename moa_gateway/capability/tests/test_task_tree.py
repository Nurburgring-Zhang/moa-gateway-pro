"""task_tree 真实测试(非 mock)"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.task_tree import (
    TaskStatus, TaskSegment, TaskTree,
    compute_aggregates, is_leaf, is_root, depth,
    get_ready_tasks, detect_cycles,
    tree_to_dict, tree_from_dict,
)


# ============ 字段 / 枚举 ============
def test_task_segment_fields():
    """TaskSegment 9 字段全定义"""
    t = TaskSegment(
        id="t1", title="hello", description="world",
        status=TaskStatus.PENDING, parent_id="p",
        children_ids=["c1", "c2"], token_cost=100,
        duration_seconds=1.5, resolution_score=0.8,
        depends_on=["d1"],
    )
    assert t.id == "t1"
    assert t.title == "hello"
    assert t.description == "world"
    assert t.status == TaskStatus.PENDING
    assert t.parent_id == "p"
    assert t.children_ids == ["c1", "c2"]
    assert t.token_cost == 100
    assert t.duration_seconds == 1.5
    assert t.resolution_score == 0.8
    assert t.depends_on == ["d1"]
    print("  ✓ test_task_segment_fields: 9 字段全在")


def test_task_status_enum():
    """TaskStatus 5 值"""
    values = {s.value for s in TaskStatus}
    assert values == {"pending", "in_progress", "completed", "failed", "blocked"}
    assert len(TaskStatus) == 5
    print(f"  ✓ test_task_status_enum: {sorted(values)}")


# ============ CRUD ============
def test_add_and_get_task():
    """add_task / get_task"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(
        id="a", title="a", description="a",
        status=TaskStatus.PENDING, parent_id="root",
    ))
    a = tree.get_task("a")
    assert a is not None
    assert a.title == "a"
    assert tree.get_task("nonexistent") is None
    # root 也应该在
    assert tree.get_task("root") is not None
    # parent 应该自动挂 children
    assert "a" in tree.get_task("root").children_ids
    print("  ✓ test_add_and_get_task: add + get + parent 自动挂载")


def test_get_children():
    """get_children — 只返直接子节点"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="b", title="b", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="a1", title="a1", description="", status=TaskStatus.PENDING, parent_id="a"))
    children = tree.get_children("root")
    assert len(children) == 2
    assert {c.id for c in children} == {"a", "b"}
    # 子节点不再被作为直接子
    grand = tree.get_children("a")
    assert len(grand) == 1
    assert grand[0].id == "a1"
    print("  ✓ test_get_children: root → [a,b], a → [a1]")


def test_get_descendants_recursive():
    """get_descendants 递归拿全部后代"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="b", title="b", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="a1", title="a1", description="", status=TaskStatus.PENDING, parent_id="a"))
    tree.add_task(TaskSegment(id="a2", title="a2", description="", status=TaskStatus.PENDING, parent_id="a"))
    tree.add_task(TaskSegment(id="a1x", title="a1x", description="", status=TaskStatus.PENDING, parent_id="a1"))
    desc = tree.get_descendants("root")
    ids = {d.id for d in desc}
    assert ids == {"a", "b", "a1", "a2", "a1x"}, f"got {ids}"
    # 不含自身
    assert all(d.id != "root" for d in desc)
    print(f"  ✓ test_get_descendants_recursive: {len(desc)} 后代")


def test_set_status():
    """set_status 改状态"""
    tree = TaskTree("root")
    tree.set_status("root", TaskStatus.IN_PROGRESS)
    assert tree.get_task("root").status == TaskStatus.IN_PROGRESS
    tree.set_status("root", TaskStatus.COMPLETED)
    assert tree.get_task("root").status == TaskStatus.COMPLETED
    print("  ✓ test_set_status: PENDING → IN_PROGRESS → COMPLETED")


def test_mark_completed_accumulates():
    """mark_completed 写入分数/时长/消耗"""
    tree = TaskTree("root")
    tree.mark_completed("root", score=0.9, duration=12.5, token_cost=2000)
    r = tree.get_task("root")
    assert r.status == TaskStatus.COMPLETED
    assert r.resolution_score == 0.9
    assert r.duration_seconds == 12.5
    assert r.token_cost == 2000
    print("  ✓ test_mark_completed_accumulates: 3 字段写入")


# ============ 树分析 ============
def test_compute_aggregates():
    """compute_aggregates 累加自身 + 后代"""
    tree = TaskTree("root")
    tree.mark_completed("root", score=0.6, duration=1.0, token_cost=100)
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.COMPLETED, parent_id="root"))
    tree.mark_completed("a", score=0.8, duration=2.0, token_cost=200)
    tree.add_task(TaskSegment(id="a1", title="a1", description="", status=TaskStatus.COMPLETED, parent_id="a"))
    tree.mark_completed("a1", score=1.0, duration=3.0, token_cost=300)
    agg = compute_aggregates(tree, "root")
    assert agg["token_cost"] == 100 + 200 + 300
    assert agg["duration_seconds"] == 1.0 + 2.0 + 3.0
    assert agg["task_count"] == 3
    # avg = (0.6+0.8+1.0)/3
    assert abs(agg["avg_resolution_score"] - 0.8) < 1e-9
    # 子树聚合
    sub = compute_aggregates(tree, "a")
    assert sub["token_cost"] == 200 + 300
    assert sub["task_count"] == 2
    print(f"  ✓ test_compute_aggregates: tokens={agg['token_cost']}, avg={agg['avg_resolution_score']}")


def test_is_leaf_and_root():
    """is_leaf / is_root"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root"))
    assert is_root(tree, "root") is True
    assert is_leaf(tree, "root") is False
    assert is_root(tree, "a") is False
    assert is_leaf(tree, "a") is True
    print("  ✓ test_is_leaf_and_root: root非leaf, a leaf非root")


def test_depth_single_layer():
    """depth 单层"""
    tree = TaskTree("root")
    assert depth(tree, "root") == 0
    print("  ✓ test_depth_single_layer: root depth=0")


def test_depth_multi_layer():
    """depth 多层"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="a1", title="a1", description="", status=TaskStatus.PENDING, parent_id="a"))
    tree.add_task(TaskSegment(id="a1x", title="a1x", description="", status=TaskStatus.PENDING, parent_id="a1"))
    assert depth(tree, "root") == 0
    assert depth(tree, "a") == 1
    assert depth(tree, "a1") == 2
    assert depth(tree, "a1x") == 3
    print("  ✓ test_depth_multi_layer: 0/1/2/3")


# ============ 依赖图 ============
def test_get_ready_tasks_no_depends():
    """无依赖 + PENDING → 全 ready"""
    tree = TaskTree("root")
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root"))
    tree.add_task(TaskSegment(id="b", title="b", description="", status=TaskStatus.PENDING, parent_id="root"))
    ready = get_ready_tasks(tree)
    assert set(ready) == {"root", "a", "b"}
    print(f"  ✓ test_get_ready_tasks_no_depends: {sorted(ready)}")


def test_get_ready_tasks_deps_done():
    """依赖都完成 → 自己 ready"""
    tree = TaskTree("root")
    tree.mark_completed("root", score=1.0, duration=0.0, token_cost=0)
    tree.add_task(TaskSegment(
        id="a", title="a", description="",
        status=TaskStatus.PENDING, parent_id="root",
        depends_on=["root"],
    ))
    ready = get_ready_tasks(tree)
    assert "a" in ready
    assert "root" not in ready  # root 已 completed
    print(f"  ✓ test_get_ready_tasks_deps_done: {sorted(ready)}")


def test_get_ready_tasks_deps_pending():
    """依赖未完成 → 不在 ready"""
    tree = TaskTree("root")
    # root 保持 PENDING
    tree.add_task(TaskSegment(
        id="a", title="a", description="",
        status=TaskStatus.PENDING, parent_id="root",
        depends_on=["root"],
    ))
    ready = get_ready_tasks(tree)
    assert "a" not in ready
    assert "root" in ready  # root 无依赖,本身是 PENDING
    print(f"  ✓ test_get_ready_tasks_deps_pending: a 不在 ready")


def test_detect_cycles_none():
    """无环 → []"""
    tree = TaskTree("root")
    tree.mark_completed("root", score=1.0, duration=0, token_cost=0)
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root", depends_on=["root"]))
    tree.add_task(TaskSegment(id="b", title="b", description="", status=TaskStatus.PENDING, parent_id="root", depends_on=["a"]))
    cycles = detect_cycles(tree)
    assert cycles == [], f"got {cycles}"
    print("  ✓ test_detect_cycles_none: []")


def test_detect_cycles_present():
    """有环 → 返回环路径"""
    tree = TaskTree("root")
    # root 依赖 a, a 依赖 root → 环
    # 注意: root 已被 TaskTree.__init__ 创建为 PENDING,parent=None
    # 我们手动调 set_status / 直接改 depends_on
    root = tree.get_task("root")
    root.depends_on = ["a"]
    tree.add_task(TaskSegment(id="a", title="a", description="", status=TaskStatus.PENDING, parent_id="root", depends_on=["root"]))
    cycles = detect_cycles(tree)
    assert len(cycles) >= 1, f"got {cycles}"
    # 环应该包含 root 和 a
    flat = [tid for cyc in cycles for tid in cyc]
    assert "root" in flat and "a" in flat
    # 首尾相同
    for cyc in cycles:
        assert cyc[0] == cyc[-1], f"环未闭合: {cyc}"
    print(f"  ✓ test_detect_cycles_present: {cycles}")


# ============ JSON 序列化 ============
def test_json_roundtrip():
    """tree_to_dict / tree_from_dict 往返一致"""
    tree = TaskTree("root")
    tree.mark_completed("root", score=0.5, duration=2.0, token_cost=100)
    tree.add_task(TaskSegment(id="a", title="a", description="alpha", status=TaskStatus.PENDING, parent_id="root", token_cost=50, depends_on=["root"]))
    tree.add_task(TaskSegment(id="b", title="b", description="beta", status=TaskStatus.IN_PROGRESS, parent_id="root", token_cost=80))
    tree.add_task(TaskSegment(id="a1", title="a1", description="", status=TaskStatus.PENDING, parent_id="a"))
    d = tree_to_dict(tree)
    # 必须是 JSON 字符串可序列化
    s = json.dumps(d)
    d2 = json.loads(s)
    tree2 = tree_from_dict(d2)
    # 检查任务数与字段
    assert len(tree2.all_tasks()) == len(tree.all_tasks())
    for orig in tree.all_tasks():
        loaded = tree2.get_task(orig.id)
        assert loaded is not None, f"丢失: {orig.id}"
        assert loaded.title == orig.title
        assert loaded.status == orig.status
        assert loaded.parent_id == orig.parent_id
        assert loaded.token_cost == orig.token_cost
        assert loaded.depends_on == orig.depends_on
    # 父子关系保留
    assert "a" in tree2.get_task("root").children_ids
    assert "a1" in tree2.get_task("a").children_ids
    print("  ✓ test_json_roundtrip: 4 任务全保留,父子关系正确")


# ============ 边界 ============
def test_boundary_zero_children():
    """边界: 0 children — 单 root,无后代"""
    tree = TaskTree("root")
    assert is_leaf(tree, "root") is True
    assert is_root(tree, "root") is True
    assert depth(tree, "root") == 0
    assert tree.get_children("root") == []
    assert tree.get_descendants("root") == []
    agg = compute_aggregates(tree, "root")
    assert agg["task_count"] == 1
    assert agg["token_cost"] == 0
    assert agg["duration_seconds"] == 0.0
    print("  ✓ test_boundary_zero_children: 单 root 全空集")


def test_boundary_single_task_tree():
    """边界: 1 task 树"""
    tree = TaskTree("solo")
    assert len(tree.all_tasks()) == 1
    assert tree.get_task("solo").id == "solo"
    ready = get_ready_tasks(tree)
    assert ready == ["solo"]
    assert detect_cycles(tree) == []
    d = tree_to_dict(tree)
    tree2 = tree_from_dict(d)
    assert tree2.get_task("solo") is not None
    print("  ✓ test_boundary_single_task_tree: 单节点全功能正常")


# ============ 主入口 ============
def main() -> None:
    tests = [
        test_task_segment_fields,
        test_task_status_enum,
        test_add_and_get_task,
        test_get_children,
        test_get_descendants_recursive,
        test_set_status,
        test_mark_completed_accumulates,
        test_compute_aggregates,
        test_is_leaf_and_root,
        test_depth_single_layer,
        test_depth_multi_layer,
        test_get_ready_tasks_no_depends,
        test_get_ready_tasks_deps_done,
        test_get_ready_tasks_deps_pending,
        test_detect_cycles_none,
        test_detect_cycles_present,
        test_json_roundtrip,
        test_boundary_zero_children,
        test_boundary_single_task_tree,
    ]
    print(f"=== task_tree: running {len(tests)} tests ===")
    passed = 0
    for fn in tests:
        try:
            if fn() is True:
                passed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"=== {passed}/{len(tests)} passed ===")
    if passed != len(tests):
        sys.exit(1)


if __name__ == "__main__":
    main()
