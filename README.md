# MoA Gateway Pro

> **v2.0** 鈥?鍟嗕笟绾?宸ヤ笟绾у妯″瀷鍗忎綔API缃戝叧
> 141涓狝PI绔偣 路 236涓祴璇曠敤渚?路 Go楂樻€ц兘浠ｇ悊 路 PostgreSQL鍙屽悗绔?路 MCP缃戝叧 路 SOC2鍚堣

宸ヤ笟绾?AI 缃戝叧:璺敱銆丮oA 鍗忎綔銆佸叡璇嗐€佽川閲忚瘎浼般€侀厤棰濄€佸彲瑙傛祴鎬с€佺煡璇嗗簱銆佸畨鍏ㄩ槻鎶ゃ€丮CP鍗忚銆佽涔夌紦瀛樸€侀珮鍙敤 鈥斺€?涓€涓?FastAPI 杩涚▼ + Go浠ｇ悊灞傛悶瀹氥€?

## 涓€鍒嗛挓涓婃墜

```powershell
# 瀹夎渚濊禆(宸叉湁 venv 鍙烦杩?
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 鍚姩
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "YourStrongPassword#2024"
$env:MOA_JWT_SECRET = "your-secret-key-minimum-32-characters-long!"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 127.0.0.1 --port 8088

# 鎵撳紑 Swagger UI
# http://127.0.0.1:8088/docs
```

浠讳綍 OpenAI 瀹㈡埛绔兘鑳界洿鎺ユ帴:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8088/v1", api_key="mgw-...")
resp = client.chat.completions.create(model="auto", messages=[{"role":"user","content":"hi"}])
```

## v2.0 鏍稿績鍗囩骇

| 缁村害 | v1.8.1 | v2.0 | 鎻愬崌 |
|------|--------|------|------|
| **鏋舵瀯** | 鍗曚綋server.py 5000琛?| 11涓矾鐢辨ā鍧?+ Go浠ｇ悊灞?| 妯″潡鍖?+ 寰绾у欢杩?|
| **鏁版嵁搴?* | SQLite only | SQLite + PostgreSQL鍙屽悗绔?| 楂樺苟鍙戝啓鍏ユ敮鎸?|
| **鏉冮檺** | 2绾?admin/user) | 4绾BAC + 15鏉冮檺 + 瀹¤鏃ュ織 | 浼佷笟绾ф潈闄愭帶鍒?|
| **缂撳瓨** | 鏃?| 涓夊眰璇箟缂撳瓨(绮剧‘+璇箟+Redis) | 闄嶆湰20-40% |
| **鍙娴?* | 鍩虹鏃ュ織 | OpenTelemetry Trace/Metrics/Logs | Grafana + 鍛婅 |
| **鍚堣** | 鏃?| SOC2: AES-256鍔犲瘑 + PII鑴辨晱 + GDPR | 浼佷笟鍚堣灏辩华 |
| **楂樺彲鐢?* | 鍗曞疄渚?| 鐔旀柇鍣?+ 鏁呴殰杞Щ + K8s Helm | 99.99% SLA |
| **MCP** | 鍩虹 | 瀹屾暣JSON-RPC Server/Client + 宸ュ叿RBAC | 瀵规爣TrueFoundry |
| **娴嬭瘯** | 0 | 236涓?100%閫氳繃) | 鍟嗕笟绾ц鐩?|
| **鎬ц兘** | 7193 RPS(health) | 636 RPS(health,鍚叏涓棿浠? | 瀹夊叏+鍙娴嬪紑閿€鍐?|

## 鏍稿績鑳藉姏

### 澶氭ā鍨嬪崗浣?(MoA)
- **3-layer / N-layer MoA** 鈥?澶氭ā鍨嬪苟琛屾彁璁?+ 鏃楄埌妯″瀷鑱氬悎
- **6 绉嶆墽琛岀瓥鐣?* 鈥?`parallel` / `compose` / `judge` / `chain` / `pipeline` / `single`
- **7 涓唴缃璁?* 鈥?`fast` / `balanced` / `quality` / `moa-balanced` / `moa-quality` / `chinese_battalion` / `pipeline`
- **澶氭ā鍨嬫姇绁?* 鈥?`vote_ensemble` / `should_rebalance` / `detect_convergent` / `arbitrate_conflicts`

### MCP缃戝叧 (v2.0鏂板)
- **MCP Server** 鈥?JSON-RPC 2.0鍗忚,宸ュ叿娉ㄥ唽/鍙戠幇/璋冪敤
- **MCP Client** 鈥?杩炴帴澶栭儴MCP Server鍙戠幇宸ュ叿
- **宸ュ叿绾BAC** 鈥?admin/operator/user/readonly鎸夎鑹茶繃婊ゅ伐鍏?
- **Tool Guardrails** 鈥?Pre/Post璋冪敤闃叉姢(鍗遍櫓妯″紡妫€娴?
- **3涓唴缃伐鍏?* 鈥?`moa_list_models` / `moa_check_quota` / `moa_route_preview`

### 璇箟缂撳瓨 (v2.0鏂板)
- **L1 绮剧‘鍖归厤** 鈥?MD5 hash,LRU娣樻卑,10K鏉＄洰
- **L2 璇箟缂撳瓨** 鈥?N-gram鍚戦噺 + 浣欏鸡鐩镐技搴?鈮?.95
- **L3 Redis鍒嗗竷寮?* 鈥?澶氬疄渚嬪叡浜?浼橀泤闄嶇骇
- **闃叉姢** 鈥?绌哄€肩紦瀛?闃茬┛閫? + TTL闅忔満鍋忕Щ(闃查洩宕?

### RBAC鏉冮檺浣撶郴 (v2.0鏂板)
- **4绾ц鑹?* 鈥?admin / operator / user / readonly
- **15椤规潈闄?* 鈥?call/chat, call/moa, read/models, write/keys, admin/rbac ...
- **瀹¤鏃ュ織** 鈥?缁撴瀯鍖朖SON,PII鑷姩鑴辨晱,HMAC绛惧悕閾?
- **鐢ㄦ埛绠＄悊API** 鈥?CRUD + 瑙掕壊鍒嗛厤

### SOC2鍚堣 (v2.0鏂板)
- **AES-256-GCM鍔犲瘑** 鈥?瀛楁绾ч潤鎬佹暟鎹姞瀵?
- **PII妫€娴?* 鈥?9绉嶆ā寮?email/鎵嬫満/淇＄敤鍗?SSN/韬唤璇?IP/API Key/JWT)
- **GDPR** 鈥?鏁版嵁鍒犻櫎(琚仐蹇樻潈) + 鏁版嵁瀵煎嚭
- **瀵嗛挜杞崲** 鈥?鍙屽瘑閽ヨ繃娓℃湡,90澶╄嚜鍔ㄦ彁閱?
- **瀹夊叏鍩虹嚎妫€鏌?* 鈥?10椤归厤缃鏌?jwt_secret/encryption/debug/cors/tls...)
- **鏁版嵁淇濈暀绛栫暐** 鈥?鑷姩娓呯悊杩囨湡鏁版嵁

### 楂樺彲鐢ㄦ灦鏋?(v2.0鏂板)
- **鐔旀柇鍣?* 鈥?CLOSED/OPEN/HALF_OPEN鐘舵€佹満
- **鏅鸿兘閲嶈瘯** 鈥?鎸囨暟閫€閬?+ 鎶栧姩
- **Provider鏁呴殰杞Щ** 鈥?浼樺厛绾ф帓搴?+ 鑷姩鍒囨崲
- **浼橀泤鍏冲仠** 鈥?璇锋眰鎺掔┖ + 瓒呮椂寮哄埗閫€鍑?
- **娣卞害鍋ュ悍妫€鏌?* 鈥?liveness / readiness / startup 涓夋帰閽?
- **Docker Compose HA** 鈥?澶氬疄渚?+ PostgreSQL + Redis + Prometheus + Grafana
- **K8s Helm Chart** 鈥?Deployment / Service / HPA / PDB

### Go楂樻€ц兘浠ｇ悊灞?(v2.0鏂板)
- **寰绾у欢杩?* 鈥?httputil.ReverseProxy闆舵嫹璐濊浆鍙?
- **JWT蹇€熼獙璇?* 鈥?Go灞傚畬鎴愮鍚嶉獙璇?涓嶈浆鍙戝埌Python
- **SSE娴佽浆鍙?* 鈥?闆剁紦鍐插疄鏃舵祦
- **浠ょ墝妗堕檺娴?* 鈥?姣廔P鐙珛妗?杩囨湡鑷姩娓呯悊
- **Prometheus鎸囨爣** 鈥?璇锋眰鏁?寤惰繜/鐘舵€佺爜

### OpenTelemetry鍙娴嬫€?(v2.0鏂板)
- **鍒嗗竷寮忚拷韪?* 鈥?姣忚姹倀race_id + span閾?
- **14+ Prometheus鎸囨爣** 鈥?LLM寤惰繜/Token鐢ㄩ噺/鎴愭湰/缂撳瓨鍛戒腑/鐔旀柇鍣?闄愭祦
- **缁撴瀯鍖栨棩蹇?* 鈥?JSON鏍煎紡,trace_id鍏宠仈
- **Grafana Dashboard** 鈥?12闈㈡澘JSON妯℃澘
- **鍛婅瑙勫垯** 鈥?10鏉rometheus鍛婅(楂樺欢杩?楂橀敊璇巼/Provider涓嶅彲鐢?

### 璺敱 + 璐ㄩ噺
- **鏅鸿兘璺敱** 鈥?鎸夋煡璇㈠鏉傚害鑷姩鍒嗛厤 fast / balanced / quality
- **Elo 鎺掑悕** 鈥?`rank_elo` 鑷姩璇勪及妯″瀷璐ㄩ噺
- **L0 璐ㄩ噺闂?* 鈥?`gate_l0` 鎷︽埅浣庤川鍝嶅簲

### 宸ュ叿闆嗘垚
- **76 涓?capability passthrough** 鈥?`secret_scan` / `fuzzy_dedup` / `anthropic_compat` ...
- **MCP 鍗忚** 鈥?JSON-RPC 2.0 Server/Client
- **WebUI** 鈥?闈欐€佹枃浠舵墭绠?鍐呯疆绠＄悊鎺у埗鍙?

## 鏋舵瀯 (v2.0)

```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹?             Go Proxy Layer (proxy/)                         鈹?
鈹? JWT蹇€熼獙璇?路 SSE娴佽浆鍙?路 浠ょ墝妗堕檺娴?路 Prometheus鎸囨爣       鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                   鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹?             FastAPI 141 routes (server.py 287琛?            鈹?
鈹? /v1/chat/completions  /v1/moa/*  /v1/mcp/*  /v1/agent/*    鈹?
鈹? + /v1/capability/* (76) + /api/admin/* + /api/auth/*       鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                   鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹? routes/ (12妯″潡) 路 rbac.py 路 audit.py 路 _helpers.py        鈹?
鈹? health 路 metrics 路 mcp 路 chat 路 moa 路 auth 路 admin 路       鈹?
鈹? capability 路 models 路 agent 路 webui 路 compliance           鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                   鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹? mcp/ 路 cache/ 路 observability/ 路 compliance/ 路 ha/         鈹?
鈹? MCP Server/Client 路 涓夊眰缂撳瓨 路 OTel涓夋敮鏌?路 SOC2 路 鐔旀柇鍣? 鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                   鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹? database.py (SQLite/PostgreSQL鍙屽悗绔? 路 storage.py         鈹?
鈹? 杩炴帴姹?路 Alembic杩佺Щ 路 16妯″瀷绔偣 路 async health check     鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
```

## 娴嬭瘯

```powershell
# 236涓祴璇曠敤渚?(100%閫氳繃)
.venv\Scripts\python -m pytest tests/ -v --tb=short

# 娴嬭瘯瑕嗙洊:
# test_core_endpoints.py  27涓?鈥?鏍稿績API绔偣闆嗘垚
# test_security_fixes.py  11涓?鈥?瀹夊叏淇楠岃瘉
# test_rbac.py            22涓?鈥?RBAC鏉冮檺鐭╅樀
# test_mcp.py             31涓?鈥?MCP鍗忚
# test_cache.py           25涓?鈥?涓夊眰缂撳瓨
# test_observability.py   27涓?鈥?OTel鍙娴嬫€?
# test_compliance.py      33涓?鈥?SOC2鍚堣
# test_ha.py              35涓?鈥?楂樺彲鐢ㄦ灦鏋?
# test_boundary.py        14涓?鈥?杈圭晫鏉′欢
# test_quality_fixes.py   11涓?鈥?浠ｇ爜璐ㄩ噺

# 鎬ц兘鍩哄噯
.venv\Scripts\python -m benchmarks.run_benchmark --concurrency 10 --duration 10
```

## 鎬ц兘鍩哄噯 (v2.0瀹炴祴)

| 鍦烘櫙 | RPS | P50 | P95 | P99 | 鎴愬姛鐜?|
|------|-----|-----|-----|-----|--------|
| /health | 636 | 12.7ms | 30.7ms | 57.3ms | 100% |
| /v1/models | 210 | 44.5ms | 62.4ms | 81.0ms | 100% |
| /api/auth/login | 190 | 46.8ms | 68.3ms | 102ms | 100% |
| /api/admin/stats | 605 | 14.9ms | 26.9ms | 38.8ms | 100% |

> 13,835娆″熀鍑嗚姹?0澶辫触銆俠crypt鐧诲綍P50=47ms绗﹀悎棰勬湡(bcrypt rounds=12)銆?

## 閮ㄧ讲

### Docker (鍗曞疄渚?

```bash
docker build -t moa-gateway-pro:v2.0 .
docker run -p 8088:8088 \
  -e MOA_ADMIN_PASSWORD=YourPassword \
  -e MOA_JWT_SECRET=your-secret-key-minimum-32-characters-long! \
  moa-gateway-pro:v2.0
```

### Docker Compose HA (鐢熶骇绾?

```bash
cd deploy/ha
# 閰嶇疆 .env (DB_PASSWORD, MOA_JWT_SECRET, MOA_ADMIN_PASSWORD)
docker-compose -f docker-compose.ha.yml up -d
# 鍚姩: 3涓悗绔?+ 2涓狦o浠ｇ悊 + PostgreSQL + Redis + Prometheus + Grafana
```

### Go浠ｇ悊灞?(楂樻€ц兘鍓嶇)

```bash
cd proxy
go build -o moa-proxy .
./moa-proxy --listen :8080 --backend http://127.0.0.1:8088
```

### K8s Helm

```bash
cd deploy/ha/helm
helm install moa-gateway . -f values.yaml
```

### 鐩存帴璺?

```powershell
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "YourStrongPassword"
$env:MOA_JWT_SECRET = "your-secret-key-minimum-32-characters-long!"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8088 --workers 4
```

### PostgreSQL (鐢熶骇鏁版嵁搴?

```bash
export DATABASE_URL="postgresql+psycopg2://moa:password@localhost:5432/moa_gateway"
export DB_POOL_SIZE=20
export DB_MAX_OVERFLOW=10
alembic upgrade head  # 棣栨杩佺Щ
```

## 閰嶇疆

`config.yaml` (榛樿) + 鐜鍙橀噺 override:

### 鏍稿績閰嶇疆
- `MOA_ADMIN_PASSWORD` 鈥?WebUI admin 瀵嗙爜
- `MOA_JWT_SECRET` 鈥?JWT绛惧悕瀵嗛挜(鈮?2瀛楃)
- `MOA_DATA_DIR` 鈥?SQLite / log 鐩綍
- `MOA_LOG_LEVEL` 鈥?DEBUG / INFO / WARNING / ERROR

### 鏁版嵁搴?
- `DATABASE_URL` 鈥?PostgreSQL杩炴帴URL(涓嶈鍒欑敤SQLite)
- `DB_POOL_SIZE` 鈥?杩炴帴姹犲ぇ灏?榛樿20)
- `DB_MAX_OVERFLOW` 鈥?杩炴帴姹犳孩鍑?榛樿10)

### 缂撳瓨
- `REDIS_URL` 鈥?Redis杩炴帴URL(涓嶈鍒欎粎鐢ㄦ湰鍦扮紦瀛?

### 鍚堣
- `MOA_ENCRYPTION_KEY` 鈥?AES-256鍔犲瘑瀵嗛挜
- `MOA_AUDIT_SIGNING_KEY` 鈥?瀹¤鏃ュ織绛惧悕瀵嗛挜
- `MOA_KEY_ROTATION_DAYS` 鈥?瀵嗛挜杞崲鍛ㄦ湡(榛樿90澶?

## 绔偣鍒嗙被

| 绫诲埆 | 鏁伴噺 | 绀轰緥 |
|---|---|---|
| OpenAI 鍏煎 | 2 | `/v1/chat/completions`, `/v1/models` |
| 鍘熺敓 MoA | 13 | `/v1/moa/execute`, `/v1/moa/eval`, `/v1/moa/presets` ... |
| MCP缃戝叧 | 6 | `/v1/mcp`, `/v1/mcp/tools`, `/v1/mcp/servers` |
| 璺敱/閰嶉 | 2 | `/v1/route/preview`, `/v1/quota` |
| Agent/Workflow | 6 | `/v1/agent/list`, `/v1/agent/dispatch` ... |
| Capability | 15 | `/v1/capability/secret-scan`, `/v1/capability/ensemble-vote` ... |
| Admin/Auth | 19 | `/api/auth/login`, `/api/admin/users`, `/api/admin/roles` ... |
| 鍚堣 | 10 | `/api/admin/compliance/baseline`, `/api/admin/compliance/gdpr/*` |
| 鍋ュ悍/鎸囨爣 | 7 | `/health`, `/health/live`, `/health/ready`, `/metrics` |
| WebUI | 1 | `/` (闈欐€佹枃浠? |
| **鍚堣** | **141** | |

## 椤圭洰缁撴瀯

```
moa-gateway-pro/
鈹溾攢鈹€ proxy/              # Go楂樻€ц兘浠ｇ悊灞?13涓枃浠?
鈹溾攢鈹€ moa_gateway/
鈹?  鈹溾攢鈹€ server.py       # FastAPI鍏ュ彛(287琛?
鈹?  鈹溾攢鈹€ routes/         # 12涓矾鐢辨ā鍧?
鈹?  鈹溾攢鈹€ mcp/            # MCP鍗忚(7涓ā鍧?
鈹?  鈹溾攢鈹€ cache/          # 涓夊眰璇箟缂撳瓨(7涓ā鍧?
鈹?  鈹溾攢鈹€ observability/  # OpenTelemetry(8涓ā鍧?
鈹?  鈹溾攢鈹€ compliance/     # SOC2鍚堣(8涓ā鍧?
鈹?  鈹溾攢鈹€ ha/             # 楂樺彲鐢?5涓ā鍧?
鈹?  鈹溾攢鈹€ rbac.py         # RBAC鏉冮檺(4瑙掕壊/15鏉冮檺)
鈹?  鈹溾攢鈹€ audit.py        # 瀹¤鏃ュ織(PII鑴辨晱)
鈹?  鈹溾攢鈹€ database.py     # SQLite/PostgreSQL鍙屽紩鎿?
鈹?  鈹斺攢鈹€ ...             # 鍏朵粬鏍稿績妯″潡
鈹溾攢鈹€ tests/              # 236涓祴璇曠敤渚?
鈹溾攢鈹€ benchmarks/         # 鍘嬫祴妗嗘灦
鈹溾攢鈹€ deploy/
鈹?  鈹溾攢鈹€ ha/             # Docker HA + K8s Helm
鈹?  鈹溾攢鈹€ monitoring/     # Grafana + Prometheus鍛婅
鈹?  鈹斺攢鈹€ database/       # PostgreSQL閮ㄧ讲
鈹斺攢鈹€ 鍙傝€?analysis/      # 11涓灦鏋勫垎鏋愭枃妗?
```

## 渚濊禆

- Python 3.11+
- FastAPI / Pydantic v2 / Uvicorn
- SQLite (寮€鍙? / PostgreSQL (鐢熶骇)
- Redis (鍙€?鍒嗗竷寮忕紦瀛?
- Go 1.22+ (鍙€?楂樻€ц兘浠ｇ悊)
- bcrypt / jose (JWT) / cryptography (AES-256)
- opentelemetry-sdk / prometheus-client

## License

MIT

## 鐗堟湰

| Version | Date | 鍏抽敭鐗规€?|
|---|---|---|
| **v2.0** | 2026-08-03 | 鍟嗕笟绾у崌绾? Go浠ｇ悊 + PostgreSQL + RBAC + MCP + 璇箟缂撳瓨 + OTel + SOC2 + HA |
| v1.8.1 | 2026-07-19 | Pydantic Field 鎻忚堪 + 绔偣绛惧悕娓呯悊 |
| v1.8.0 | 2026-07-18 | 83 绔偣 Pydantic 鍖?+ 90 OpenAPI schemas |
| v1.7.5 | 2026-07-18 | Final release + 7193 RPS |
| v1.7.0 | 2026-07-18 | Service Layer + AgentDispatch + Workflow |

瀹屾暣鍙樻洿瑙?[CHANGELOG.md](CHANGELOG.md)
