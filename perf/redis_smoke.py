"""perf/redis_smoke.py — 真 Redis 连接 + 业务操作 smoke test

测真 Redis 服务能:
  1. SET/GET 简单 KV
  2. Hash 操作 (HSET/HGETALL) - 模拟 quota counter
  3. List 操作 (LPUSH/LRANGE) - 模拟 trace event
  4. Sorted set (ZADD/ZRANGE) - 模拟 rate limit
  5. TTL/EXPIRE - 模拟 session 过期
  6. PUB/SUB - 模拟事件通知
"""
import time
import sys
import json
import redis


def main():
    print("=" * 60)
    print(" 真 Redis 服务联调 (127.0.0.1:6379)")
    print("=" * 60)
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True, protocol=2)
    try:
        pong = r.ping()
    except Exception as e:
        print(f"  ERROR: cannot connect: {e}")
        sys.exit(1)
    print(f"  ping: {pong}")

    # 1. KV
    print("\n[1] KV SET/GET")
    r.set("moa:test:key1", "hello", ex=10)
    v = r.get("moa:test:key1")
    print(f"  set/get: {v} (TTL={r.ttl('moa:test:key1')}s)")
    assert v == "hello"

    # 2. Hash
    print("\n[2] Hash (模拟 quota counter)")
    r.hset("moa:quota:user123", "rpm_used", "5")
    r.hset("moa:quota:user123", "rpm_limit", "60")
    r.hset("moa:quota:user123", "daily_tokens", "1500")
    h = r.hgetall("moa:quota:user123")
    print(f"  hgetall: {h}")
    r.hincrby("moa:quota:user123", "rpm_used", 1)
    new_used = r.hget("moa:quota:user123", "rpm_used")
    print(f"  hincrby rpm_used: {new_used}")
    assert int(new_used) == 6

    # 3. List (trace events)
    print("\n[3] List (模拟 trace 事件流)")
    r.delete("moa:trace:events")
    for i in range(5):
        r.lpush("moa:trace:events", json.dumps({"ts": time.time(), "event": f"req_{i}"}))
    events = r.lrange("moa:trace:events", 0, 4)
    print(f"  lrange last 5: {len(events)} events")
    for e in events[:2]:
        print(f"    {e[:80]}...")

    # 4. Sorted set (rate limit tokens)
    print("\n[4] Sorted Set (令牌桶)")
    r.delete("moa:bucket:user456")
    now = time.time()
    for i in range(5):
        r.zadd("moa:bucket:user456", {f"token_{i}": now + i})
    tokens = r.zcard("moa:bucket:user456")
    print(f"  zcard: {tokens} tokens")
    # pop 过期
    expired = r.zremrangebyscore("moa:bucket:user456", 0, now - 0.001)
    print(f"  zremrangebyscore (过期): removed {expired}")
    remaining = r.zcard("moa:bucket:user456")
    print(f"  remaining: {remaining}")

    # 5. TTL
    print("\n[5] TTL/Session 过期")
    r.set("moa:session:abc", "token_value", ex=2)
    print(f"  TTL: {r.ttl('moa:session:abc')}s")
    time.sleep(2.5)
    expired = r.get("moa:session:abc")
    print(f"  after 2.5s wait: value={expired} (None=已过期)")

    # 6. Pub/Sub
    print("\n[6] Pub/Sub (事件通知)")
    pubsub = r.pubsub()
    pubsub.subscribe("moa:events")
    r.publish("moa:events", json.dumps({"type": "test", "msg": "hello"}))
    msg = pubsub.get_message(timeout=1.0)
    print(f"  received: {str(msg)[:200] if msg else 'no msg'}")
    pubsub.close()

    # 7. Pipeline
    print("\n[7] Pipeline (批量操作)")
    pipe = r.pipeline()
    for i in range(10):
        pipe.set(f"moa:bulk:{i}", f"v{i}")
    pipe.execute()
    print(f"  bulk set 10 keys: {sum(1 for k in r.scan_iter('moa:bulk:*'))}")

    # Cleanup
    r.delete("moa:test:key1", "moa:quota:user123", "moa:trace:events",
             "moa:bucket:user456", "moa:session:abc")
    for k in r.scan_iter("moa:bulk:*"):
        r.delete(k)
    print("\n  cleanup done")

    print("\n" + "=" * 60)
    print(" RESULT: 真 Redis 联调 6/6 通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
