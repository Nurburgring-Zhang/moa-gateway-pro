"""moa_gateway.ui.pages — 6 个功能页面(flet 控件)

每个 build_* 返回 flet Control 作为页面内容
"""

from __future__ import annotations

import contextlib
import logging

import flet as ft

logger = logging.getLogger(__name__)


# ========== 通用组件 ==========
def page_header(palette, title: str, sub: str = "") -> ft.Container:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(title, size=28, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text(sub, size=13, color=palette.text_dim) if sub else ft.Container(),
            ],
            spacing=4,
        ),
        padding=ft.padding.only(left=32, right=32, top=28, bottom=12),
    )


def card(
    palette, title: str = "", sub: str = "", content: ft.Control | None = None
) -> ft.Container:
    title_row = (
        ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=palette.text)
                        if title
                        else ft.Container(),
                        ft.Text(sub, size=11, color=palette.text_dim) if sub else ft.Container(),
                    ],
                    spacing=2,
                ),
            ],
        )
        if title or sub
        else None
    )

    body = []
    if title_row:
        body.append(title_row)
    if content:
        body.append(content)
    return ft.Container(
        content=ft.Column(controls=body, spacing=12),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
    )


def stat_card(palette, label: str, value: str = "—") -> ft.Container:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(label, size=11, weight=ft.FontWeight.W_500, color=palette.text_dim),
                ft.Text(value, size=32, weight=ft.FontWeight.BOLD, color=palette.text),
            ],
            spacing=6,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.symmetric(horizontal=20, vertical=18),
        expand=True,
    )


def primary_button(palette, label: str, on_click=None) -> ft.ElevatedButton:
    return ft.ElevatedButton(
        label,
        bgcolor=palette.accent,
        color="#ffffff",
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
        ),
        on_click=on_click,
    )


def ghost_button(palette, label: str, on_click=None) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label,
        style=ft.ButtonStyle(
            side=ft.BorderSide(1, palette.border_strong),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=16, vertical=10),
            color=palette.text,
        ),
        on_click=on_click,
    )


def badge(palette, text: str, kind: str = "info") -> ft.Container:
    color_map = {
        "info": (palette.accent, palette.accent_dim),
        "success": (palette.success, "rgba(52,199,89,0.16)"),
        "warning": (palette.warning, "rgba(255,159,10,0.16)"),
        "danger": (palette.danger, "rgba(255,69,58,0.16)"),
    }
    fg, bg = color_map.get(kind, color_map["info"])
    return ft.Container(
        content=ft.Text(text, size=10, weight=ft.FontWeight.BOLD, color=fg),
        bgcolor=bg,
        border_radius=6,
        padding=ft.padding.symmetric(horizontal=8, vertical=2),
    )


# ========== HTTP 异步 helper ==========
async def http_get(url: str, timeout: float = 10, token: str = None) -> dict:
    import httpx

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url, headers=headers)
        return r.json() if r.status_code == 200 else {"error": r.text}


async def http_post(url: str, payload: dict, timeout: float = 60, token: str = None) -> dict:
    import httpx

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload, headers=headers)
        return r.json() if r.status_code in (200, 201) else {"error": r.text}


# ========== 1. 仪表盘 ==========
def build_dashboard(state: dict, sr) -> ft.Control:
    palette = state["palette"]
    base = f"http://127.0.0.1:{sr.port or 8765}"

    # 4 个统计卡
    val_endpoints = ft.Text("—", size=32, weight=ft.FontWeight.BOLD, color=palette.text)
    val_healthy = ft.Text("—", size=32, weight=ft.FontWeight.BOLD, color=palette.text)
    val_keys = ft.Text("—", size=32, weight=ft.FontWeight.BOLD, color=palette.text)
    val_cost = ft.Text("—", size=32, weight=ft.FontWeight.BOLD, color=palette.text)

    stat_row = ft.Row(
        controls=[
            stat_card_with_value(palette, "端点数", val_endpoints),
            stat_card_with_value(palette, "健康端点", val_healthy),
            stat_card_with_value(palette, "API 密钥", val_keys),
            stat_card_with_value(palette, "累计花费", val_cost),
        ],
        spacing=12,
    )

    # 端点列表
    ep_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID", color=palette.text_sec)),
            ft.DataColumn(ft.Text("模型", color=palette.text_sec)),
            ft.DataColumn(ft.Text("状态", color=palette.text_sec)),
            ft.DataColumn(ft.Text("延迟", color=palette.text_sec)),
        ],
        rows=[],
        border=ft.border.all(1, palette.border),
        border_radius=10,
        column_spacing=20,
    )

    def refresh(e=None):
        if not sr.is_running:
            return

        async def do():
            try:
                models = await http_get(f"{base}/v1/models", token=sr.admin_token)
                endpoints = await http_get(f"{base}/api/endpoints", token=sr.admin_token)
                keys = await http_get(f"{base}/api/api-keys", token=sr.admin_token)
                stats = await http_get(f"{base}/api/stats", token=sr.admin_token)
                return models, endpoints, keys, stats
            except Exception as ex:
                logger.error("dashboard refresh: %s", ex)
                return None, None, None, None

        # 在 event loop 里跑
        data = sr.call_async(do(), timeout=10)
        if data is None:
            logger.error("dashboard result: call_async returned None")
            return
        if data[0] is None:
            return
        models, endpoints, keys, stats = data
        val_endpoints.value = str(len(models.get("data", [])))
        eps = endpoints.get("endpoints", [])
        healthy = sum(1 for e in eps if e.get("health_status") == "healthy")
        val_healthy.value = f"{healthy} / {len(eps)}"
        val_keys.value = str(len(keys.get("keys", [])))
        cost = stats.get("total_cost", 0)
        val_cost.value = f"${cost:.2f}"
        # 表格
        rows = []
        for ep in eps[:20]:
            h = ep.get("health_status", "")
            h_color = (
                palette.success
                if h == "healthy"
                else (palette.danger if h == "unhealthy" else palette.text_dim)
            )
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(ep.get("id", ""), color=palette.text)),
                        ft.DataCell(ft.Text(ep.get("model", ""), color=palette.text)),
                        ft.DataCell(ft.Text(h, color=h_color)),
                        ft.DataCell(
                            ft.Text(f"{ep.get('avg_latency_ms', 0):.0f}ms", color=palette.text)
                        ),
                    ]
                )
            )
        ep_table.rows = rows
        ep_table.update()

    # 启动时自动刷新
    sr.on_started_callbacks.append(
        lambda: __import__("threading").Thread(target=refresh, daemon=True).start()
    )

    # 自动定时刷新
    def periodic():
        import time

        while True:
            time.sleep(10)
            with contextlib.suppress(Exception):
                refresh()

    import threading

    threading.Thread(target=periodic, daemon=True).start()

    # 端点表 card
    ep_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("端点状态", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text("实时健康监测", size=11, color=palette.text_dim),
                ep_table,
            ],
            spacing=8,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
        expand=2,
    )

    # 活动 card
    activity_text = ft.Text("暂无记录", size=11, color=palette.text_dim)
    activity_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("最近调用", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text("实时 MoA 执行记录", size=11, color=palette.text_dim),
                activity_text,
            ],
            spacing=8,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
        expand=1,
    )

    # 快捷入口
    def on_shortcut(key):
        # 切页面通过 main.switch_page 但 main 在外层作用域
        # 这里简单实现:通过全局事件,实际 main 有 switch_page
        # 暂用占位:在 build_pages 里统一处理
        pass

    shortcuts = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("快捷入口", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.GridView(
                    controls=[
                        ghost_button(palette, "🧠 试玩台"),
                        ghost_button(palette, "📈 跑 Benchmark"),
                        ghost_button(palette, "🔌 加端点"),
                        ghost_button(palette, "📝 写 Prompt"),
                    ],
                    runs_count=2,
                    max_extent=200,
                    child_aspect_ratio=2.5,
                    spacing=8,
                ),
            ],
            spacing=8,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
        expand=1,
    )

    right_col = ft.Column(
        controls=[activity_card, shortcuts],
        spacing=12,
        expand=1,
    )

    body = ft.Column(
        controls=[
            page_header(palette, "仪表盘", "总览 · 端点健康 · MoA 流量 · 系统状态"),
            ft.Container(content=stat_row, padding=ft.padding.symmetric(horizontal=32, vertical=8)),
            ft.Container(
                content=ft.Row(
                    controls=[ep_card, right_col],
                    spacing=12,
                    expand=True,
                ),
                padding=ft.padding.symmetric(horizontal=32, vertical=8),
                expand=True,
            ),
        ],
        spacing=0,
        expand=True,
    )
    return body


def stat_card_with_value(palette, label: str, value_ctrl: ft.Text) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(label, size=11, weight=ft.FontWeight.W_500, color=palette.text_dim),
                value_ctrl,
            ],
            spacing=6,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.symmetric(horizontal=20, vertical=18),
        expand=True,
    )


# ========== 2. 模型端点 ==========
def build_endpoints(state: dict, sr) -> ft.Control:
    palette = state["palette"]
    base = f"http://127.0.0.1:{sr.port or 8765}"

    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID", color=palette.text_sec)),
            ft.DataColumn(ft.Text("名称", color=palette.text_sec)),
            ft.DataColumn(ft.Text("Provider", color=palette.text_sec)),
            ft.DataColumn(ft.Text("Tier", color=palette.text_sec)),
            ft.DataColumn(ft.Text("启用", color=palette.text_sec)),
            ft.DataColumn(ft.Text("健康", color=palette.text_sec)),
            ft.DataColumn(ft.Text("操作", color=palette.text_sec)),
        ],
        rows=[],
        border=ft.border.all(1, palette.border),
        border_radius=10,
        column_spacing=16,
    )
    summary = ft.Text("—", size=11, color=palette.text_dim)

    endpoints_data = []

    def refresh(e=None):
        if not sr.is_running:
            summary.value = "⚠️ 请先在顶部启动服务"
            summary.update()
            return

        async def do():
            try:
                return await http_get(f"{base}/api/endpoints", token=sr.admin_token)
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=10)
        if data is None:
            return
        if "error" in data:
            summary.value = f"❌ {data['error']}"
            summary.update()
            return
        endpoints_data.clear()
        endpoints_data.extend(data.get("endpoints", []))
        rows = []
        for i, ep in enumerate(endpoints_data):
            enabled = ep.get("enabled", True)
            h = ep.get("health_status", "unknown")
            h_color = (
                palette.success
                if h == "healthy"
                else (palette.danger if h == "unhealthy" else palette.text_dim)
            )
            actions = ft.Row(
                controls=[
                    ghost_button(palette, "编辑", lambda _, idx=i: open_edit(idx)),
                    ghost_button(
                        palette, "停用" if enabled else "启用", lambda _, idx=i: toggle(idx)
                    ),
                ],
                spacing=4,
            )
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(ep.get("id", ""), color=palette.text)),
                        ft.DataCell(ft.Text(ep.get("name", ""), color=palette.text)),
                        ft.DataCell(ft.Text(ep.get("provider", ""), color=palette.text)),
                        ft.DataCell(ft.Text(ep.get("tier", ""), color=palette.text)),
                        ft.DataCell(
                            ft.Text(
                                "✓" if enabled else "✗",
                                color=palette.success if enabled else palette.text_dim,
                            )
                        ),
                        ft.DataCell(ft.Text(h, color=h_color)),
                        ft.DataCell(actions),
                    ]
                )
            )
        table.rows = rows
        summary.value = f"共 {len(endpoints_data)} 个端点"
        table.update()
        summary.update()

    def open_edit(idx: int | None):
        ep = endpoints_data[idx] if idx is not None else None
        is_new = ep is None
        title = "新增端点" if is_new else f"编辑: {ep.get('id', '')}"

        id_e = ft.TextField(value=ep.get("id", "") if ep else "", label="ID", width=400)
        name_e = ft.TextField(value=ep.get("name", "") if ep else "", label="名称", width=400)
        provider_e = ft.Dropdown(
            label="Provider",
            value=(ep.get("provider", "openai") if ep else "openai"),
            options=[
                ft.dropdown.Option(p)
                for p in [
                    "openai",
                    "anthropic",
                    "deepseek",
                    "zhipu",
                    "moonshot",
                    "qwen",
                    "doubao",
                    "lingyi",
                    "baichuan",
                    "mistral",
                    "google",
                    "mock",
                ]
            ],
            width=400,
        )
        model_e = ft.TextField(value=ep.get("model", "") if ep else "", label="Model", width=400)
        api_base_e = ft.TextField(
            value=ep.get("api_base", "") if ep else "", label="API Base", width=400
        )
        api_key_e = ft.TextField(
            value=ep.get("api_key", "") if ep else "",
            label="API Key / Env",
            password=True,
            width=400,
        )
        tier_e = ft.Dropdown(
            label="Tier",
            value=(ep.get("tier", "standard") if ep else "standard"),
            options=[
                ft.dropdown.Option(t) for t in ["free", "lite", "standard", "premium", "flagship"]
            ],
            width=400,
        )
        enabled_e = ft.Switch(label="启用", value=ep.get("enabled", True) if ep else True)
        msg = ft.Text("", size=12, color=palette.danger)

        def save(close_dlg):
            # 修25: UI 字段名对齐 server schema (EndpointUpsert)
            payload = {
                "endpoint_id": id_e.value.strip(),
                "name": name_e.value.strip() or id_e.value.strip(),
                "provider": provider_e.value,
                "model": model_e.value.strip(),
                "api_base": api_base_e.value.strip(),
                "api_key_plain": api_key_e.value.strip(),
                "tier": tier_e.value,
                "enabled": enabled_e.value,
                "weight": 100,
                "max_tokens": 8192,
                "cost_per_1k_input": 0.001,
                "cost_per_1k_output": 0.002,
                "tags": [],
            }
            if not payload["endpoint_id"] or not payload["provider"] or not payload["model"]:
                msg.value = "ID/Provider/Model 必填"
                msg.update()
                return

            async def do():
                try:
                    if is_new:
                        return await http_post(
                            f"{base}/api/endpoints", payload, timeout=15, token=sr.admin_token
                        )
                    else:
                        return await http_post(
                            f"{base}/api/endpoints/{ep['id']}",
                            payload,
                            timeout=15,
                            token=sr.admin_token,
                        )
                except Exception as ex:
                    return {"error": str(ex)}

            data = sr.call_async(do(), timeout=15)
            if data is None:
                data = {"error": "call_async timeout/failed"}
            if "error" in data:
                msg.value = f"❌ {str(data['error'])[:100]}"
                msg.update()
            else:
                close_dlg(True)
                refresh()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        id_e,
                        name_e,
                        provider_e,
                        model_e,
                        api_base_e,
                        api_key_e,
                        tier_e,
                        enabled_e,
                        msg,
                    ],
                    tight=True,
                    spacing=8,
                ),
                width=440,
                height=480,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: page.close(dlg)),
                ft.ElevatedButton(
                    "保存",
                    bgcolor=palette.accent,
                    color="#ffffff",
                    on_click=lambda _: save(close_dlg=lambda ok: page.close(dlg) if ok else None),
                ),
            ],
        )
        page = ft.app_ref.page if hasattr(ft, "app_ref") else None
        # 通过 outer page 弹出
        # 用 page.open 是 flet 0.27 后的 API
        if page:
            page.open(dlg)
        else:
            # 退化:直接添加到 page
            ft.app_ref.page.controls.append(dlg) if hasattr(ft, "app_ref") else None

    def toggle(idx):
        ep = endpoints_data[idx]
        eid = ep.get("id", "")
        if not eid:
            return

        async def do():
            try:
                return await http_post(
                    f"{base}/api/endpoints/{eid}/toggle", {}, token=sr.admin_token
                )
            except Exception as ex:
                return {"error": str(ex)}

        sr.call_async(do(), timeout=10, token=sr.admin_token)
        refresh()

    # 启动刷新
    sr.on_started_callbacks.append(
        lambda: __import__("threading").Thread(target=refresh, daemon=True).start()
    )

    toolbar = ft.Row(
        controls=[
            primary_button(palette, "+ 新增端点", lambda _: open_edit(None)),
            ghost_button(palette, "🔄 刷新", refresh),
        ],
        spacing=8,
    )

    return ft.Column(
        controls=[
            page_header(palette, "模型端点", "管理所有 LLM 端点 + API Key"),
            ft.Container(content=toolbar, padding=ft.padding.symmetric(horizontal=32, vertical=8)),
            ft.Container(
                content=ft.Column(
                    controls=[table, summary],
                    spacing=8,
                ),
                padding=ft.padding.symmetric(horizontal=32, vertical=8),
                expand=True,
            ),
        ],
        spacing=0,
        expand=True,
    )


# ========== 3. MoA 编排(试玩台) ==========
def build_playground(state: dict, sr) -> ft.Control:
    palette = state["palette"]
    base = f"http://127.0.0.1:{sr.port or 8765}"

    preset_dd = ft.Dropdown(
        label="Preset",
        options=[],
        width=320,
        value=None,
    )
    strategy_desc = ft.Text("—", size=11, color=palette.text_dim)
    spin_n = ft.TextField(
        label="参考模型数", value="3", width=320, keyboard_type=ft.KeyboardType.NUMBER
    )
    spin_tref = ft.TextField(
        label="参考温度", value="0.6", width=320, keyboard_type=ft.KeyboardType.NUMBER
    )
    spin_tagg = ft.TextField(
        label="聚合温度", value="0.4", width=320, keyboard_type=ft.KeyboardType.NUMBER
    )
    spin_crit = ft.TextField(
        label="Critic 轮数", value="1", width=320, keyboard_type=ft.KeyboardType.NUMBER
    )
    spin_layer = ft.TextField(
        label="层数 (layered)", value="3", width=320, keyboard_type=ft.KeyboardType.NUMBER
    )

    txt_input = ft.TextField(
        label="输入问题",
        multiline=True,
        min_lines=3,
        max_lines=5,
        value="用 Python 写一个 LRU Cache,要求 O(1) 复杂度。",
        text_size=13,
    )
    txt_output = ft.TextField(
        label="输出结果",
        multiline=True,
        min_lines=10,
        read_only=True,
        value="点击运行查看结果…",
        text_size=12,
    )
    meta = ft.Text("等待运行", size=11, color=palette.text_dim)

    presets = []

    def load_presets(e=None):
        if not sr.is_running:
            return

        async def do():
            try:
                return await http_get(f"{base}/v1/moa/presets", token=sr.admin_token)
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=10)
        if data is None:
            return
        if "error" in data:
            return
        presets.clear()
        presets.extend(data.get("presets", []))
        preset_dd.options = [
            ft.dropdown.Option(
                p["name"],
                f"{p['name']} · {p['strategy']}"
                + (f" ({p['layer_count']}L)" if p.get("layer_count", 1) > 1 else ""),
            )
            for p in presets
        ]
        if presets:
            preset_dd.value = presets[0]["name"]
            on_preset_change(None)
        preset_dd.update()

    def on_preset_change(e):
        if not preset_dd.value:
            return
        p = next((x for x in presets if x["name"] == preset_dd.value), None)
        if not p:
            return
        strategy_desc.value = (
            f"{p.get('description') or p['strategy']}\n"
            f"参考: {p.get('reference_count', '?')}  ·  聚合器: {p.get('aggregator') or '动态'}  ·  "
            f"层数: {p.get('layer_count', 1)}"
        )
        spin_n.value = str(p.get("reference_count", 3))
        spin_tref.value = str(p.get("reference_temperature", 0.6))
        spin_tagg.value = str(p.get("aggregator_temperature", 0.4))
        spin_crit.value = str(p.get("critic_rounds", 1))
        spin_layer.value = str(p.get("layer_count", 3))
        for w in (strategy_desc, spin_n, spin_tref, spin_tagg, spin_crit, spin_layer):
            with contextlib.suppress(Exception):
                w.update()

    preset_dd.on_change = on_preset_change

    def on_run(e):
        query = (txt_input.value or "").strip()
        if not query:
            meta.value = "⚠️ 请输入问题"
            meta.update()
            return
        if not sr.is_running:
            meta.value = "⚠️ 请先启动服务"
            meta.update()
            return
        preset_name = preset_dd.value
        meta.value = "运行中..."
        meta.update()
        try:
            payload = {
                "model": "moa",
                "messages": [{"role": "user", "content": query}],
                "preset": preset_name,
                "reference_count": int(spin_n.value or 3),
                "reference_temperature": float(spin_tref.value or 0.6),
                "aggregator_temperature": float(spin_tagg.value or 0.4),
                "critic_rounds": int(spin_crit.value or 1),
                "layer_count": int(spin_layer.value or 3),
            }
        except Exception as ex:
            meta.value = f"⚠️ 参数错误: {ex}"
            meta.update()
            return

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/chat/completions", payload, timeout=180, token=sr.admin_token
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=180)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            meta.value = f"❌ {str(data['error'])[:80]}"
            meta.update()
            return
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        txt_output.value = content or "(无输出)"
        meta.value = f"preset: {preset_name}  ·  tokens: {usage.get('total_tokens', 0)}"
        txt_output.update()
        meta.update()

    sr.on_started_callbacks.append(
        lambda: __import__("threading").Thread(target=load_presets, daemon=True).start()
    )

    # 左侧配置
    left = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Preset", size=11, weight=ft.FontWeight.BOLD, color=palette.text_sec),
                preset_dd,
                strategy_desc,
                ft.Divider(color=palette.border, height=1),
                spin_n,
                spin_tref,
                spin_tagg,
                spin_crit,
                spin_layer,
                ft.Container(expand=True),
                ft.Container(
                    content=primary_button(palette, "▶ 运行", on_run),
                    width=320,
                ),
            ],
            spacing=10,
        ),
        width=360,
        padding=ft.padding.all(20),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
    )

    # 右侧
    right = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column([txt_input], spacing=0),
                padding=ft.padding.all(16),
                bgcolor=palette.card,
                border_radius=14,
                border=ft.border.all(1, palette.border),
            ),
            ft.Container(
                content=ft.Column([txt_output, meta], spacing=8),
                padding=ft.padding.all(16),
                bgcolor=palette.card,
                border_radius=14,
                border=ft.border.all(1, palette.border),
                expand=True,
            ),
        ],
        spacing=12,
        expand=True,
    )

    return ft.Column(
        controls=[
            page_header(palette, "MoA 编排", "选 preset · 调参数 · 跑多模型协作 · 看每层真实输出"),
            ft.Container(
                content=ft.Row(
                    controls=[left, right],
                    spacing=12,
                    expand=True,
                ),
                padding=ft.padding.symmetric(horizontal=32, vertical=8),
                expand=True,
            ),
        ],
        spacing=0,
        expand=True,
    )
