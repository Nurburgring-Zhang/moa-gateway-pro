"""P0-2: Replace `Model(**dict)` with `Model.from_dict(dict)` + try/except -> 422."""
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

replacements = [
    # TierStat
    ('stats = {k: TierStat(**v) for k, v in body.get("stats", {}).items()}',
     'try:\n            stats = {k: TierStat.from_dict(v) for k, v in body.get("stats", {}).items()}\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid stats: {e}") from e'),
    # Channel
    ('channels = [Channel(**c) for c in body.get("channels", [])]',
     'try:\n            channels = [Channel.from_dict(c) for c in body.get("channels", [])]\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid channels: {e}") from e'),
    # Aggregator (moa-n-layer)
    ('aggregators = [Aggregator(**a) for a in body.get("aggregators", [])]',
     'try:\n            aggregators = [Aggregator.from_dict(a) for a in body.get("aggregators", [])]\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid aggregators: {e}") from e'),
    # PolicyRule
    ('rules = [PolicyRule(**r) for r in body.get("rules", [])]',
     'try:\n            rules = [PolicyRule.from_dict(r) for r in body.get("rules", [])]\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid rules: {e}") from e'),
    # HealthMetrics
    ('metrics_list = [HealthMetrics(**m) for m in body.get("providers", [])]',
     'try:\n            metrics_list = [HealthMetrics.from_dict(m) for m in body.get("providers", [])]\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid providers: {e}") from e'),
    # IterationRecord
    ('rec = IterationRecord(**body.get("record", {}))',
     'try:\n            rec = IterationRecord.from_dict(body.get("record", {}))\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid record: {e}") from e'),
    # RequestContext
    ('ctx = RequestContext(**body.get("context", {"query": ""}))',
     'try:\n            ctx = RequestContext.from_dict(body.get("context", {"query": ""}))\n        except (TypeError, KeyError, ValueError) as e:\n            raise HTTPException(422, f"invalid context: {e}") from e'),
]

count = 0
for old, new in replacements:
    if old in src:
        src = src.replace(old, new, 1)
        count += 1
        print(f"OK: {old[:60]}")
    else:
        print(f"MISS: {old[:60]}")

p.write_text(src, encoding="utf-8")
print(f"\nTotal replaced: {count}")
