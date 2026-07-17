"""Add from_dict classmethods to 8 dataclasses for server.py field aliasing.

Approach: Append classmethod to each class. Idempotent (skip if already present).
"""
import re
from pathlib import Path

# Map: file -> [(class_name, real_fields, alias_map)]
# alias_map: input_field -> real_field (or list of accepted input fields)
EDITS = [
    # 1) TierStat: server sends (tier, endpoint_count, success_count, fail_count, weight_sum)
    #    real: (tier, endpoint_count, success_count, total_calls, avg_latency_ms, avg_cost, last_24h_calls, cooldown_count)
    ("moa_gateway/capability/consensus.py", "TierStat", {
        "tier": "tier",
        "endpoint_count": "endpoint_count",
        "success_count": "success_count",
        "total_calls": ["total_calls", "fail_count"],
        "avg_latency_ms": "avg_latency_ms",
        "avg_cost": ["avg_cost", "weight_sum"],
        "last_24h_calls": "last_24h_calls",
        "cooldown_count": "cooldown_count",
    }),
    # 2) Channel: server sends (name, cost_per_1k_input, cost_per_1k_output, tier)
    #    real: (name, cost_per_1k_input, cost_per_1k_output, avg_latency_ms, reliability)
    ("moa_gateway/capability/cost_estimator.py", "Channel", {
        "name": "name",
        "cost_per_1k_input": "cost_per_1k_input",
        "cost_per_1k_output": "cost_per_1k_output",
        "avg_latency_ms": ["avg_latency_ms", "tier"],
        "reliability": ["reliability", "tier"],  # tier doesn't fit; use 0.5 default
    }),
    # 3) Aggregator: server sends (name, model_id, role)
    #    real: (name, model_id, synthesis_prompt)
    ("moa_gateway/capability/n_layer_moa.py", "Aggregator", {
        "name": "name",
        "model_id": "model_id",
        "synthesis_prompt": ["synthesis_prompt", "role"],
    }),
    # 4) PolicyRule: server sends (pattern, action, priority)
    #    real: (name, action, pattern, match_type, reason)
    ("moa_gateway/capability/action_policy.py", "PolicyRule", {
        "name": ["name", "pattern"],  # server sends pattern as name
        "action": "action",
        "pattern": ["pattern", "name"],
        "match_type": ["match_type", "priority"],  # priority -> match_type
        "reason": ["reason", "priority"],
    }),
    # 5) HealthMetrics: server sends (provider, total_calls, success_calls, fail_calls, avg_latency_ms, p99_latency_ms, consecutive_failures, circuit_open)
    #    real: (provider, total_calls, success_count, failure_count, rate_limit_hits, consecutive_429s, consecutive_failures, avg_latency_ms, p95_latency_ms, last_error_type, last_success_at, last_failure_at, breaker_open)
    ("moa_gateway/capability/provider_health.py", "HealthMetrics", {
        "provider": "provider",
        "total_calls": "total_calls",
        "success_count": ["success_count", "success_calls"],
        "failure_count": ["failure_count", "fail_calls"],
        "rate_limit_hits": "rate_limit_hits",
        "consecutive_429s": "consecutive_429s",
        "consecutive_failures": "consecutive_failures",
        "avg_latency_ms": "avg_latency_ms",
        "p95_latency_ms": ["p95_latency_ms", "p99_latency_ms"],
        "last_error_type": "last_error_type",
        "last_success_at": "last_success_at",
        "last_failure_at": "last_failure_at",
        "breaker_open": ["breaker_open", "circuit_open"],
    }),
    # 6) IterationRecord: server uses positional kwargs via **body
    #    real: (iter_idx, proposals, panel_scores, convergent_ideas, conflicts_resolved, selected_proposal_idx, timestamp)
    #    server can pass: iter_idx, proposals, panel_scores, summary, etc. — flexible
    ("moa_gateway/capability/feedback_loop.py", "IterationRecord", {
        "iter_idx": ["iter_idx", "iteration"],
        "proposals": "proposals",
        "panel_scores": "panel_scores",
        "convergent_ideas": ["convergent_ideas", "ideas"],
        "conflicts_resolved": "conflicts_resolved",
        "selected_proposal_idx": ["selected_proposal_idx", "selected_idx", "best_idx"],
        "timestamp": "timestamp",
    }),
    # 7) RequestContext: server uses RequestContext(**body.get("context", {"query": ""}))
    #    real: (request_id, query, required_capabilities, max_cost_per_1k, max_latency_ms, priority)
    ("moa_gateway/capability/consumption_intel.py", "RequestContext", {
        "request_id": ["request_id", "id"],
        "query": "query",
        "required_capabilities": "required_capabilities",
        "max_cost_per_1k": ["max_cost_per_1k", "cost"],
        "max_latency_ms": ["max_latency_ms", "latency"],
        "priority": "priority",
    }),
    # 8) TaskSegment: real: (id, title, description, status, parent_id, ...)
    ("moa_gateway/capability/task_tree.py", "TaskSegment", {
        "id": "id",
        "title": "title",
        "description": "description",
        "status": "status",
        "parent_id": ["parent_id", "parent"],
        "children_ids": "children_ids",
        "token_cost": "token_cost",
        "duration_seconds": "duration_seconds",
        "resolution_score": "resolution_score",
        "depends_on": "depends_on",
    }),
]


def make_from_dict(name, alias_map, real_fields):
    """Build a from_dict classmethod body."""
    body = f"    @classmethod\n    def from_dict(cls, d: dict) -> '{name}':\n"
    body += f'        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""\n'
    body += "        kwargs = {}\n"
    for real_field, accepted in alias_map.items():
        if isinstance(accepted, str):
            accepted = [accepted]
        # First try each accepted name in order
        for input_name in accepted:
            if input_name == real_field:
                body += f'        if "{input_name}" in d: kwargs["{real_field}"] = d["{input_name}"]\n'
            else:
                # Real field is different from input — fall through
                pass
        # Add a coalesce check
        if len(accepted) == 1:
            body += f'        if "{real_field}" not in kwargs and "{accepted[0]}" in d: kwargs["{real_field}"] = d["{accepted[0]}"]\n'
        else:
            for alt in accepted:
                if alt != real_field:
                    body += f'        if "{real_field}" not in kwargs and "{alt}" in d: kwargs["{real_field}"] = d["{alt}"]\n'
    body += "        return cls(**kwargs)\n"
    return body


total = 0
for fpath, classname, alias_map in EDITS:
    p = Path(r"D:\MoA Gateway Pro") / fpath
    if not p.exists():
        print(f"MISSING: {fpath}")
        continue
    content = p.read_text(encoding="utf-8")
    if f"class {classname}" not in content:
        print(f"NO CLASS {classname} in {fpath}")
        continue
    if f"def from_dict" in content and classname in content:
        # already added
        print(f"SKIP {fpath} (already has from_dict)")
        continue
    # Find class end (next class at same indent or end of file)
    pattern = rf"(@dataclass\nclass {classname}[^:]*:[^\n]*\n(?:[ \t]+[^\n]*\n){{1,30}})"
    m = re.search(pattern, content)
    if not m:
        # Try just class line
        m = re.search(rf"(class {classname}[^:]*:[^\n]*\n(?:[ \t]+[^\n]*\n){{1,30}})", content)
    if not m:
        print(f"NO MATCH {fpath} class {classname}")
        continue
    block = m.group(0)
    # Build from_dict
    from_dict_body = make_from_dict(classname, alias_map, alias_map.keys())
    new_block = block + "\n" + from_dict_body
    content = content.replace(block, new_block)
    p.write_text(content, encoding="utf-8")
    print(f"OK {fpath}: added from_dict for {classname}")
    total += 1

print(f"\nTotal: {total} files updated")
