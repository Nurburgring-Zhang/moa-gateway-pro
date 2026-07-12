"""server 端到端 - 4 preset"""
import json
import urllib.request


def main():
    # get key
    from moa_gateway.storage import get_storage
    s = get_storage()
    k = s.create_api_key(name="e2e_4p", quota_rpm=1000, quota_daily_tokens=999999999)
    key = k["key"]
    print(f"key: {key}")

    for preset in ["chinese_battalion", "chinese_battalion_layered", "qwen_single_proposer", "ranker_qwen110b"]:
        body = json.dumps({
            "model": "moa",
            "messages": [{"role": "user", "content": "写一个 LRU Cache"}],
            "preset": preset,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8901/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            r = urllib.request.urlopen(req, timeout=30)
            data = json.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            print(f"  {preset:30s} status={r.status} len={len(content)}")
        except Exception as e:
            print(f"  {preset:30s} ERR: {e}")


if __name__ == "__main__":
    main()