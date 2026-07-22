"""moa_gateway.ui.pages2 — Benchmark / Prompts / Settings(flet 控件)"""

from __future__ import annotations

import contextlib
import math

import flet as ft

from .pages import http_get, http_post, page_header, primary_button
from .theme import DARK, THEMES

logger = ...


def svg_to_image(svg: str, width: int = 720, height: int = 400) -> ft.Container:
    """把 SVG 字符串转成 flet Image(data URL)"""
    import base64

    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    src = f"data:image/svg+xml;base64,{b64}"
    return ft.Image(src=src, width=width, height=height, fit=ft.ImageFit.CONTAIN)


def html_to_image(html: str, width: int = 800, height: int = 400) -> ft.Container:
    """把 HTML 字符串转成 flet Image(data URL,作为 SVG image)"""
    import base64

    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    src = f"data:text/html;base64,{b64}"
    # flet Image 支持 data URL,但 WebView 也支持
    return ft.Image(src=src, width=width, height=height, fit=ft.ImageFit.CONTAIN)


# ========== 4. Benchmark ==========
def build_benchmark(state: dict, sr) -> ft.Control:
    palette = state["palette"]
    base = f"http://127.0.0.1:{sr.port or 8765}"

    # 4 个 tab 内容
    suite_view = ft.Column([], spacing=12, expand=True)
    pareto_view = ft.Column([], spacing=12, expand=True)
    layered_view = ft.Column([], spacing=12, expand=True)
    flask_view = ft.Column([], spacing=12, expand=True)
    stack = ft.Column(controls=[suite_view], expand=True)

    def switch_tab(idx):
        stack.controls = [[suite_view, pareto_view, layered_view, flask_view][idx]]
        for i, b in enumerate(seg_btns):
            if i == idx:
                b.style = ft.ButtonStyle(
                    bgcolor=palette.bg_tertiary
                    if hasattr(palette, "bg_tertiary")
                    else DARK.bg_tertiary,
                    color=palette.text,
                    shape=ft.RoundedRectangleBorder(radius=7),
                )
            else:
                b.style = ft.ButtonStyle(
                    bgcolor=None,
                    color=palette.text_sec,
                    shape=ft.RoundedRectangleBorder(radius=7),
                )
        stack.update()

    seg_btns = []
    for i, label in enumerate(["📋 Suite", "📈 Pareto", "🌊 Layered", "🎯 FLASK"]):
        b = ft.TextButton(
            label,
            on_click=lambda _, idx=i: switch_tab(idx),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=7),
                padding=ft.padding.symmetric(horizontal=14, vertical=6),
            ),
        )
        seg_btns.append(b)
        if i == 0:
            b.style = ft.ButtonStyle(
                bgcolor=DARK.bg_tertiary,
                color=palette.text,
                shape=ft.RoundedRectangleBorder(radius=7),
            )

    # Tab 1: Suite
    cb_reasoning = ft.Checkbox(label="推理", value=True, fill_color=palette.accent)
    cb_code = ft.Checkbox(label="代码", value=True, fill_color=palette.accent)
    cb_chinese = ft.Checkbox(label="中文", value=True, fill_color=palette.accent)
    cb_creative = ft.Checkbox(label="创作", value=True, fill_color=palette.accent)
    cb_pro = ft.Checkbox(label="专业", value=True, fill_color=palette.accent)
    spin_limit = ft.TextField(
        label="每类题数", value="2", width=120, keyboard_type=ft.KeyboardType.NUMBER
    )
    suite_summary = ft.Text("选类别 + 点击 跑分", size=11, color=palette.text_dim)
    suite_table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text("题目", color=palette.text_sec))],
        rows=[],
        border=ft.border.all(1, palette.border),
        border_radius=10,
    )

    def run_suite(e):
        cats = []
        for cb, k in [
            (cb_reasoning, "reasoning"),
            (cb_code, "code"),
            (cb_chinese, "chinese"),
            (cb_creative, "creative"),
            (cb_pro, "professional"),
        ]:
            if cb.value:
                cats.append(k)
        if not cats:
            return
        category = "all" if len(cats) == 5 else ",".join(cats)
        limit = int(spin_limit.value or 2)
        if not sr.is_running:
            suite_summary.value = "⚠️ 请先启动服务"
            suite_summary.update()
            return
        suite_summary.value = "跑分中..."
        suite_summary.update()

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/moa/benchmark",
                    {
                        "presets": ["fast", "balanced", "chinese_battalion"],
                        "category": category,
                        "limit": limit,
                    },
                    timeout=300,
                    token=sr.admin_token,
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=300)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            suite_summary.value = f"❌ {str(data['error'])[:100]}"
            suite_summary.update()
            return
        # 渲染
        summary = data.get("summary", {})
        results = data.get("results", {})
        prompts = data.get("prompts", [])
        preset_names = list(results.keys())
        lines = ["📊 总览:"]
        for p, s in summary.items():
            if "error" in s:
                lines.append(f"  · {p}: ❌ {s['error'][:50]}")
            else:
                lines.append(
                    f"  · {p}: {s.get('avg_flask_score', 0):.1f}  "
                    f"成功率 {s.get('success_rate', 0) * 100:.0f}%  "
                    f"${s.get('total_cost', 0):.4f}"
                )
        suite_summary.value = "\n".join(lines)
        # 表格
        columns = [ft.DataColumn(ft.Text("题目", color=palette.text_sec))]
        for p in preset_names:
            columns.append(ft.DataColumn(ft.Text(p, color=palette.text_sec)))
        suite_table.columns = columns
        rows = []
        for p in prompts:
            cells = [
                ft.DataCell(ft.Text(f"[{p['category']}] {p['text'][:50]}...", color=palette.text))
            ]
            for preset in preset_names:
                items = results[preset]
                cell = next((i for i in items if i.get("prompt_id") == p["id"]), None)
                if cell:
                    score = cell.get("flask_avg") or 0
                    txt = f"{score:.1f}" if cell.get("success") else "❌"
                    color = (
                        palette.success
                        if score >= 80
                        else (palette.warning if score >= 60 else palette.text_dim)
                    )
                    cells.append(ft.DataCell(ft.Text(txt, color=color)))
                else:
                    cells.append(ft.DataCell(ft.Text("—", color=palette.text_dim)))
            rows.append(ft.DataRow(cells=cells))
        suite_table.rows = rows
        suite_summary.update()
        suite_table.update()

    suite_toolbar = ft.Row(
        controls=[
            cb_reasoning,
            cb_code,
            cb_chinese,
            cb_creative,
            cb_pro,
            spin_limit,
            primary_button(palette, "🚀 开始跑分", run_suite),
        ],
        spacing=10,
        wrap=True,
    )
    suite_view.controls = [
        suite_toolbar,
        suite_summary,
        ft.Container(content=suite_table, expand=True),
    ]

    # Tab 2: Pareto
    pareto_presets = ft.TextField(
        label="preset 列表(逗号)",
        value="fast,balanced,quality,chinese_battalion",
        width=400,
    )
    pareto_summary = ft.Text("输入 preset + 点击 分析", size=11, color=palette.text_dim)
    pareto_image = ft.Container(
        content=ft.Text("等待分析", color=palette.text_dim),
        alignment=ft.alignment.center,
        height=400,
        bgcolor=palette.bg_tertiary if hasattr(palette, "bg_tertiary") else DARK.bg_tertiary,
        border_radius=10,
    )

    def run_pareto(e):
        if not sr.is_running:
            pareto_summary.value = "⚠️ 请先启动服务"
            pareto_summary.update()
            return
        presets = [p.strip() for p in (pareto_presets.value or "").split(",") if p.strip()]
        if len(presets) < 2:
            pareto_summary.value = "⚠️ 至少 2 个 preset"
            pareto_summary.update()
            return
        pareto_summary.value = "分析中..."
        pareto_summary.update()
        prompts = [
            "什么是 Transformer 架构?",
            "用 Python 写二分查找",
            "请用一句话介绍李白",
            "实现 LRU Cache",
            "什么是 OAuth 2.0?",
        ]

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/moa/cost-pareto",
                    {"prompts": prompts, "presets": presets},
                    timeout=300,
                    token=sr.admin_token,
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=300)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            pareto_summary.value = f"❌ {str(data['error'])[:100]}"
            pareto_summary.update()
            return
        points = [p for p in data.get("pareto_points", []) if "error" not in p]
        frontier = data.get("pareto_frontier", [])
        recommended = data.get("recommended")
        pareto_summary.value = (
            f"Pareto 前沿: {' → '.join(frontier) or '—'}    推荐: {recommended or '—'}"
        )
        # 画 SVG
        svg_str = draw_pareto_svg(points, frontier, recommended, palette)
        pareto_image.content = svg_to_image(svg_str, 720, 400)
        pareto_summary.update()
        pareto_image.update()

    pareto_toolbar = ft.Row(
        controls=[
            pareto_presets,
            primary_button(palette, "📊 分析", run_pareto),
        ],
        spacing=10,
    )
    pareto_view.controls = [
        pareto_toolbar,
        pareto_summary,
        pareto_image,
    ]

    # Tab 3: Layered
    layered_preset = ft.Dropdown(label="Preset", options=[], width=300)
    layered_query = ft.TextField(
        label="Query",
        value="用一句话介绍 Transformer 架构",
        width=400,
    )
    layered_result = ft.Container(
        content=ft.Text("点击运行查看流程图与每层输出", color=palette.text_dim),
        alignment=ft.alignment.center,
        height=400,
        bgcolor=palette.bg_tertiary,
        border_radius=10,
    )

    def load_layered_presets():
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
        opts = []
        for p in data.get("presets", []):
            if p["strategy"] in ("layered", "parallel", "compose"):
                opts.append(ft.dropdown.Option(p["name"], f"{p['name']} ({p['strategy']})"))
        layered_preset.options = opts
        if opts:
            layered_preset.value = opts[0].key
        layered_preset.update()

    def run_layered(e):
        if not sr.is_running:
            return
        query = layered_query.value or "test"
        preset = layered_preset.value
        if not preset:
            return

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/chat/completions",
                    {
                        "model": "moa",
                        "messages": [{"role": "user", "content": query}],
                        "preset": preset,
                    },
                    timeout=120,
                    token=sr.admin_token,
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=120)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            layered_result.content = (
                f"<div style='color:#ff453a;padding:20px;'>❌ {data['error']}</div>"
            )
            layered_result.update()
            return
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        layered_result.content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            content or "(无)",
                            font_family="JetBrains Mono, monospace",
                            size=12,
                        ),
                        padding=ft.padding.all(16),
                        bgcolor=palette.bg_tertiary,
                        border_radius=10,
                        height=380,
                    ),
                    ft.Text(
                        f"tokens: {usage.get('total_tokens', 0)}  ·  preset: {preset}",
                        size=11,
                        color=palette.text_dim,
                    ),
                ],
                spacing=8,
            ),
        )
        layered_result.update()

    sr.on_started_callbacks.append(
        lambda: __import__("threading").Thread(target=load_layered_presets, daemon=True).start()
    )

    layered_toolbar = ft.Row(
        controls=[
            layered_preset,
            layered_query,
            primary_button(palette, "▶ 运行 + 画流程图", run_layered),
        ],
        spacing=10,
        wrap=True,
    )
    layered_view.controls = [layered_toolbar, ft.Container(content=layered_result, expand=True)]

    # Tab 4: FLASK
    flask_query = ft.TextField(label="问题", value="什么是 Transformer 架构?", width=600)
    flask_response = ft.TextField(
        label="回答",
        multiline=True,
        min_lines=4,
        value=(
            "Transformer 是一种基于自注意力(self-attention)机制的神经网络架构,"
            "由 Vaswani 等人在 2017 年提出。它抛弃了 RNN/CNN,完全依赖 attention,"
            "实现并行训练,广泛应用于 NLP 和 CV 任务。"
        ),
        width=600,
    )
    flask_result = ft.Container(
        content=ft.Text("点击评分查看 12 维雷达图", color=palette.text_dim),
        alignment=ft.alignment.center,
        height=400,
        bgcolor=palette.bg_tertiary,
        border_radius=10,
    )

    def run_flask(e):
        if not sr.is_running:
            return
        if not flask_query.value or not flask_response.value:
            return

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/moa/flask",
                    {
                        "query": flask_query.value,
                        "response": flask_response.value,
                    },
                    timeout=120,
                    token=sr.admin_token,
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=120)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            flask_result.content = (
                f"<div style='color:#ff453a;padding:20px;'>❌ {data['error']}</div>"
            )
            flask_result.update()
            return
        scores = data.get("scores", {})
        avg = data.get("average_0_100", 0)
        if not scores:
            flask_result.content = ft.Column(
                controls=[
                    ft.Text(
                        f"Judge 模型未返回有效 JSON。Avg: {avg}", size=12, color=palette.text_sec
                    ),
                    ft.Text(
                        data.get("raw_response", "")[:500],
                        size=11,
                        color=palette.text_dim,
                        font_family="JetBrains Mono, monospace",
                    ),
                ],
                spacing=8,
            )
            flask_result.update()
            return
        svg_str = draw_flask_svg(scores, palette)
        # 雷达图用 SVG image,表格用 flet Container + Rows
        score_rows = []
        for dim, v_ in scores.items():
            s = v_.get("score_1_5", 0) if isinstance(v_, dict) else v_
            r_ = v_.get("reason", "") if isinstance(v_, dict) else ""
            color = palette.success if s >= 4 else (palette.warning if s >= 3 else palette.danger)
            score_rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text(dim, size=12, color=palette.text, width=120),
                            ft.Text(
                                f"{s}/5", size=12, weight=ft.FontWeight.BOLD, color=color, width=40
                            ),
                            ft.Text(r_[:80] or "", size=11, color=palette.text_dim, expand=True),
                        ],
                        spacing=8,
                    ),
                    padding=ft.padding.symmetric(vertical=4),
                    border=ft.border.only(bottom=ft.BorderSide(1, palette.border)),
                )
            )

        flask_result.content = ft.Row(
            controls=[
                svg_to_image(svg_str, 400, 400),
                ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text(
                                    f"{avg:.1f}",
                                    size=32,
                                    weight=ft.FontWeight.BOLD,
                                    color=palette.text,
                                ),
                                ft.Text("/ 100", size=14, color=palette.text_dim),
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                        ft.Text(
                            f"Judge: {data.get('judge_model', '?')}",
                            size=11,
                            color=palette.text_dim,
                        ),
                        ft.Container(
                            content=ft.Column(controls=score_rows, spacing=0),
                            margin=ft.margin.only(top=12),
                            expand=True,
                        ),
                    ],
                    expand=True,
                    spacing=4,
                ),
            ],
            spacing=24,
            alignment=ft.MainAxisAlignment.START,
        )
        flask_result.update()

    flask_toolbar = ft.Column(
        controls=[
            flask_query,
            flask_response,
            primary_button(palette, "🎯 FLASK 12 维评分", run_flask),
        ],
        spacing=10,
    )
    flask_view.controls = [flask_toolbar, ft.Container(content=flask_result, expand=True)]

    # Segmented tab
    seg = ft.Container(
        content=ft.Row(controls=seg_btns, spacing=2),
        padding=3,
        border_radius=9,
        bgcolor=DARK.bg_tertiary,
        border=ft.border.all(1, palette.border),
    )
    seg_wrap = ft.Container(content=seg, padding=ft.padding.symmetric(horizontal=32, vertical=8))

    return ft.Column(
        controls=[
            page_header(
                palette, "Benchmark 可视化", "Suite 跑分 / Cost Pareto / Layered 流程 / FLASK 雷达"
            ),
            seg_wrap,
            ft.Container(
                content=stack, padding=ft.padding.symmetric(horizontal=32, vertical=8), expand=True
            ),
        ],
        spacing=0,
        expand=True,
    )


def draw_pareto_svg(points, frontier, recommended, palette) -> str:
    """画散点图 SVG"""
    if not points:
        return "<div style='color:#888;text-align:center;padding:40px;'>无数据</div>"
    costs = [p["avg_cost"] for p in points]
    scores = [p["avg_score"] for p in points]
    max_c, min_c = max(costs) * 1.2, 0
    max_s, min_s = max(scores, default=100), min(scores, default=0) - 5

    w, h = 720, 400
    px, py, iw, ih = 70, 30, w - 100, h - 80

    def to_x(c):
        return px + (c - min_c) / max(1e-6, (max_c - min_c)) * iw

    def to_y(s):
        return py + ih - (s - min_s) / max(1e-6, (max_s - min_s)) * ih

    svg = f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">'
    for i in range(0, 6):
        yy, xx = py + i * ih / 5, px + i * iw / 5
        svg += f'<line x1="{px}" y1="{yy}" x2="{w - 30}" y2="{yy}" stroke="rgba(128,128,128,0.2)"/>'
        svg += (
            f'<line x1="{xx}" y1="{py}" x2="{xx}" y2="{py + ih}" stroke="rgba(128,128,128,0.2)"/>'
        )
        svg += f'<text x="{px - 8}" y="{yy + 4}" font-size="10" text-anchor="end" fill="#888">{max_s - i * (max_s - min_s) / 5:.0f}</text>'
        svg += f'<text x="{xx}" y="{py + ih + 18}" font-size="10" text-anchor="middle" fill="#888">${i * (max_c - min_c) / 5:.4f}</text>'
    if len(frontier) > 1:
        front_pts = sorted(
            [p for p in points if p["preset"] in frontier], key=lambda p: p["avg_cost"]
        )
        path = "M " + " L ".join(
            f"{to_x(p['avg_cost']):.1f} {to_y(p['avg_score']):.1f}" for p in front_pts
        )
        svg += f'<path d="{path}" fill="none" stroke="#5e9cff" stroke-width="2" stroke-dasharray="5,3" opacity="0.7"/>'
    for p in points:
        cx, cy = to_x(p["avg_cost"]), to_y(p["avg_score"])
        is_front = p["preset"] in frontier
        is_rec = p["preset"] == recommended
        color = "#34c759" if is_rec else ("#5e9cff" if is_front else "#888")
        r = 9 if is_rec else (7 if is_front else 5)
        svg += f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{color}" stroke="#fff" stroke-width="2"/>'
        svg += f'<text x="{cx + 12:.1f}" y="{cy + 4:.1f}" font-size="11" fill="currentColor" font-weight="{"bold" if is_rec else "normal"}">{p["preset"]}{" ⭐" if is_rec else ""}</text>'
    svg += "</svg>"
    return svg


def draw_flask_svg(scores, palette) -> str:
    """画 FLASK 雷达图 SVG"""
    dims = list(scores.keys())
    n = len(dims)
    cx, cy, R = 200, 200, 130

    def angle(i):
        return i * 2 * math.pi / n - math.pi / 2

    svg = '<svg width="400" height="400" xmlns="http://www.w3.org/2000/svg">'
    for r in range(1, 6):
        pts = " ".join(
            f"{cx + math.cos(angle(i)) * R * r / 5:.1f},{cy + math.sin(angle(i)) * R * r / 5:.1f}"
            for i in range(n)
        )
        svg += f'<polygon points="{pts}" fill="none" stroke="rgba(128,128,128,0.3)"/>'
    for i, dim in enumerate(dims):
        a = angle(i)
        lx, ly = cx + math.cos(a) * (R + 25), cy + math.sin(a) * (R + 25)
        svg += f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10" text-anchor="middle" fill="currentColor">{dim[:10]}</text>'
    poly_pts = " ".join(
        f"{cx + math.cos(angle(i)) * R * (scores[dim].get('score_1_5', 0) if isinstance(scores[dim], dict) else 0) / 5:.1f},"
        f"{cy + math.sin(angle(i)) * R * (scores[dim].get('score_1_5', 0) if isinstance(scores[dim], dict) else 0) / 5:.1f}"
        for i, dim in enumerate(dims)
    )
    svg += f'<polygon points="{poly_pts}" fill="rgba(94,156,255,0.3)" stroke="#5e9cff" stroke-width="2"/>'
    svg += "</svg>"
    return svg


# ========== 5. Prompts ==========
def build_prompts(state: dict, sr) -> ft.Control:
    palette = state["palette"]
    base = f"http://127.0.0.1:{sr.port or 8765}"

    template_list = ft.ListView(width=280, spacing=2, height=600)
    editor = ft.TextField(
        label="模板内容",
        multiline=True,
        min_lines=20,
        read_only=False,
        text_size=12,
    )
    lbl_name = ft.Text("—", size=16, weight=ft.FontWeight.BOLD, color=palette.text)
    lbl_meta = ft.Text("", size=11, color=palette.text_dim)
    templates: list[dict] = []
    current_name: list[str | None] = [None]

    def load_templates(e=None):
        if not sr.is_running:
            return

        async def do():
            try:
                return await http_get(f"{base}/v1/moa/prompts", token=sr.admin_token)
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=10)
        if data is None:
            return
        if "error" in data:
            return
        templates.clear()
        templates.extend(data.get("templates", []))
        template_list.controls.clear()
        for t in templates:
            item = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(t["name"], size=12, weight=ft.FontWeight.BOLD, color=palette.text),
                        ft.Text(f"{t['source']} · {t['size']}B", size=10, color=palette.text_dim),
                    ],
                    spacing=2,
                ),
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
                border_radius=6,
                ink=True,
                on_click=lambda _, name=t["name"]: select_template(name),
            )
            template_list.controls.append(item)
        if templates:
            select_template(templates[0]["name"])
        template_list.update()

    def select_template(name: str):
        current_name[0] = name
        lbl_name.value = name
        t = next((x for x in templates if x["name"] == name), None)
        if t:
            lbl_meta.value = f"{t['source']} · {t['size']}B"
        # 加载内容
        if not sr.is_running:
            return

        async def do():
            try:
                return await http_get(f"{base}/v1/moa/prompts/{name}", token=sr.admin_token)
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=10)
        if data is None:
            return
        if "error" not in data:
            editor.value = data.get("content", "")
            editor.update()
        lbl_name.update()
        lbl_meta.update()

    def on_save(e):
        if not current_name[0]:
            return

        async def do():
            try:
                return await http_post(
                    f"{base}/v1/moa/prompts/{current_name[0]}",
                    {"content": editor.value or ""},
                    timeout=10,
                    token=sr.admin_token,
                )
            except Exception as ex:
                return {"error": str(ex)}

        data = sr.call_async(do(), timeout=10)
        if data is None:
            data = {"error": "call_async timeout/failed"}
        if "error" in data:
            page = state.get("page_ref")
            if page:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"❌ {data['error']}"), bgcolor=palette.danger
                )
                page.snack_bar.open = True
                page.update()
        else:
            page = state.get("page_ref")
            if page:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"✓ 已保存 {current_name[0]}"), bgcolor=palette.success
                )
                page.snack_bar.open = True
                page.update()
            load_templates()

    def on_delete(e):
        if not current_name[0]:
            return
        t = next((x for x in templates if x["name"] == current_name[0]), None)
        if not t or t["source"] != "user":
            return

        async def do():
            try:
                r = await http_post(
                    f"{base}/v1/moa/prompts/{current_name[0]}/__delete",
                    {},
                    timeout=10,
                    token=sr.admin_token,
                )
                return r
            except Exception:
                # 用 httpx DELETE
                import httpx

                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.delete(f"{base}/v1/moa/prompts/{current_name[0]}")
                    return r.json() if r.status_code == 200 else {"error": r.text}

        sr.call_async(do(), timeout=10)
        load_templates()

    sr.on_started_callbacks.append(
        lambda: __import__("threading").Thread(target=load_templates, daemon=True).start()
    )

    left = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("模板列表", size=13, weight=ft.FontWeight.BOLD, color=palette.text_sec),
                template_list,
            ],
            spacing=8,
        ),
        width=300,
        padding=ft.padding.all(12),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
    )

    right = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        lbl_name,
                        lbl_meta,
                        ft.Container(expand=True),
                        primary_button(palette, "💾 保存", on_save),
                        ft.ElevatedButton(
                            "🗑️ 删除", bgcolor=palette.danger, color="#ffffff", on_click=on_delete
                        ),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                editor,
            ],
            spacing=10,
            expand=True,
        ),
        padding=ft.padding.all(16),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        expand=True,
    )

    return ft.Column(
        controls=[
            page_header(palette, "Prompt 模板", "12 个真实可热更模板"),
            ft.Container(
                content=ft.Row(controls=[left, right], spacing=12, expand=True),
                padding=ft.padding.symmetric(horizontal=32, vertical=8),
                expand=True,
            ),
        ],
        spacing=0,
        expand=True,
    )


# ========== 6. Settings ==========
def build_settings(state: dict, sr, apply_theme, toggle_server, server_runner) -> ft.Control:
    palette = state["palette"]

    def set_theme(name):
        apply_theme(name)

    theme_btns = []
    theme_group = ft.Row(spacing=8)
    for k in ["auto", "light", "dark"]:
        btn = ft.ElevatedButton(
            f"{THEMES[k]['icon']} {THEMES[k]['name']}",
            bgcolor=palette.bg_tertiary if k != "auto" else palette.accent,
            color="#ffffff" if k == "auto" else palette.text,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
            on_click=lambda _, n=k: set_theme(n),
        )
        theme_btns.append(btn)
        theme_group.controls.append(btn)

    server_status = ft.Text("—", size=12, color=palette.text_dim)

    def refresh_server_status():
        if server_runner.is_running:
            server_status.value = (
                f"🟢 运行中  ·  :{server_runner.port}  ·  http://127.0.0.1:{server_runner.port}"
            )
        else:
            server_status.value = "⚪ 未启动"
        with contextlib.suppress(Exception):
            server_status.update()

    import threading

    def periodic():
        import time

        while True:
            with contextlib.suppress(Exception):
                refresh_server_status()
            time.sleep(2)

    threading.Thread(target=periodic, daemon=True).start()
    refresh_server_status()

    def change_pw(e):
        page = state.get("page_ref")
        old_e = ft.TextField(label="当前密码", password=True, width=300)
        new_e = ft.TextField(label="新密码(>=6 位)", password=True, width=300)
        msg = ft.Text("", size=12, color=palette.danger)

        def do_save(_):
            if not old_e.value or not new_e.value:
                msg.value = "请填写完整"
                msg.update()
                return
            if len(new_e.value) < 6:
                msg.value = "新密码至少 6 位"
                msg.update()
                return
            if not sr.is_running:
                msg.value = "请先启动服务"
                msg.update()
                return

            async def do():
                try:
                    return await http_post(
                        f"http://127.0.0.1:{sr.port or 8765}/api/auth/change-password",
                        {"old_password": old_e.value, "new_password": new_e.value},
                        token=sr.admin_token,
                    )
                except Exception as ex:
                    return {"error": str(ex)}

            data = sr.call_async(do(), timeout=10)
            if data is None:
                data = {"error": "call_async timeout/failed"}
            if "error" in data:
                msg.value = f"❌ {str(data['error'])[:80]}"
                msg.update()
            else:
                page.close(dlg)
                if page:
                    page.snack_bar = ft.SnackBar(
                        content=ft.Text("✓ 密码已修改"), bgcolor=palette.success
                    )
                    page.snack_bar.open = True
                    page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("修改管理员密码"),
            content=ft.Container(
                content=ft.Column(controls=[old_e, new_e, msg], spacing=10, tight=True),
                width=340,
                height=200,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: page.close(dlg)),
                ft.ElevatedButton(
                    "保存", bgcolor=palette.accent, color="#ffffff", on_click=do_save
                ),
            ],
        )
        if page:
            page.open(dlg)

    theme_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("主题", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text("Light / Dark / Auto 跟随系统", size=11, color=palette.text_dim),
                theme_group,
            ],
            spacing=12,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
    )
    server_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("服务器", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text("管理内嵌 server + 密码", size=11, color=palette.text_dim),
                server_status,
                primary_button(palette, "🔑 修改管理员密码", change_pw),
            ],
            spacing=10,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
    )
    about_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("关于", size=16, weight=ft.FontWeight.BOLD, color=palette.text),
                ft.Text("MoA Gateway Pro v1.2", size=11, color=palette.text_dim),
                ft.Text(
                    "🏗️ 9 种 MoA strategy  ·  12 preset  ·  16 模型端点  ·  12 维 FLASK  ·  7 adapter",
                    size=11,
                    color=palette.text_sec,
                ),
                ft.Text(
                    "💡 对齐 Together AI MoA + OpenSquilla + Hermes 委员会",
                    size=11,
                    color=palette.text_sec,
                ),
                ft.Text(
                    "⚖️ MIT License  ·  跨平台 (Windows / macOS / Linux)",
                    size=11,
                    color=palette.text_sec,
                ),
            ],
            spacing=6,
        ),
        bgcolor=palette.card,
        border_radius=14,
        border=ft.border.all(1, palette.border),
        padding=ft.padding.all(20),
    )

    body = ft.Column(
        controls=[
            page_header(palette, "系统设置", "主题 · 服务器 · 备份"),
            ft.Container(
                content=ft.Column(
                    controls=[theme_card, server_card, about_card],
                    spacing=12,
                ),
                padding=ft.padding.symmetric(horizontal=32, vertical=8),
            ),
        ],
        spacing=0,
        expand=True,
    )
    return body
