"""moa_gateway.ui.main — flet 桌面应用主程序

iOS 风格 · 玻璃拟态 · Light/Dark/Auto 三主题 · 跨平台
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime

import flet as ft

from .pages import (
    build_dashboard,
    build_endpoints,
    build_playground,
)
from .pages2 import build_benchmark, build_prompts, build_settings
from .server_runner import ServerRunner
from .theme import DARK, LIGHT, THEMES, make_dark_theme, make_flet_theme

logger = logging.getLogger(__name__)


def main(page: ft.Page):
    """flet 入口函数"""
    # 窗口配置
    page.title = "MoA Gateway Pro"
    page.window.width = 1440
    page.window.height = 900
    page.window.min_width = 1280
    page.window.min_height = 800
    page.padding = 0
    page.spacing = 0

    # 状态
    state = {
        "theme": "auto",  # 用户设置(auto / light / dark)
        "palette": DARK,  # 当前实际色板
        "current_page": "dashboard",
        "presets": [],
    }

    # server runner
    server_runner = ServerRunner()

    # 主题应用
    def apply_theme(theme_name: str, update_picker: bool = True):
        state["theme"] = theme_name
        if theme_name == "auto":
            # 跟随系统
            sys_dark = page.platform_brightness in (ft.Brightness.DARK, "DARK")
            # flet 不同版本 API 略不同,这里简化:用系统判断或 fallback dark
            state["palette"] = DARK if sys_dark else LIGHT
        elif theme_name == "light":
            state["palette"] = LIGHT
        else:
            state["palette"] = DARK
        p = state["palette"]
        page.theme_mode = ft.ThemeMode.DARK if p is DARK else ft.ThemeMode.LIGHT
        page.theme = make_dark_theme(p) if p is DARK else make_flet_theme(p, page.theme_mode)
        page.bgcolor = p.bg
        # 重建内容以应用主题
        rebuild_ui()
        # 同步状态栏主题选择器
        if update_picker and theme_picker in page.controls:
            theme_picker.value = theme_name
        if update_picker and status_theme_picker in status_bar.content.controls:
            status_theme_picker.value = theme_name
        page.update()

    # ========== UI 组件构造 ==========
    # 状态栏组件
    status_time = ft.Text("", size=12, weight=ft.FontWeight.W_600)
    status_dot = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor=DARK.success,
    )
    status_text = ft.Text("未启动", size=11, color=DARK.text_dim)
    status_theme_picker = ft.Dropdown(
        width=140,
        value="auto",
        options=[ft.dropdown.Option(k, f"{v['icon']} {v['name']}") for k, v in THEMES.items()],
        on_change=lambda e: apply_theme(e.control.value),
        dense=True,
        text_size=11,
    )
    start_btn = ft.ElevatedButton(
        "▶ 启动服务",
        bgcolor=DARK.accent,
        color="#ffffff",
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        on_click=lambda _: toggle_server(),
    )

    status_bar = ft.Container(
        content=ft.Row(
            controls=[
                status_time,
                ft.Container(expand=True),
                status_dot,
                status_text,
                ft.VerticalDivider(width=1, color=DARK.border),
                status_theme_picker,
                start_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=DARK.surface,
        height=38,
        padding=ft.padding.symmetric(horizontal=16),
        border=ft.border.only(bottom=ft.BorderSide(1, DARK.border)),
    )

    # 侧栏
    nav_buttons: dict[str, ft.Control] = {}
    page_views: dict[str, ft.Control] = {}

    def make_nav_item(key: str, icon: str, text: str):
        btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(icon, size=16, color=DARK.text_sec),
                    ft.Text(text, size=13, weight=ft.FontWeight.W_500, color=DARK.text_sec),
                ],
                spacing=10,
            ),
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            border_radius=10,
            ink=True,
            on_click=lambda _, k=key: switch_page(k),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        nav_buttons[key] = btn
        return btn

    sidebar = ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text("MoA Gateway Pro", size=17, weight=ft.FontWeight.BOLD),
                            ft.Text(
                                "MULTI-MODEL ORCHESTRATION",
                                size=10,
                                color=DARK.text_dim,
                                weight=ft.FontWeight.W_500,
                            ),
                        ],
                        spacing=2,
                    ),
                    padding=ft.padding.only(left=20, right=20, top=18, bottom=14),
                ),
                ft.Column(
                    controls=[
                        make_nav_item("dashboard", "📊", "仪表盘"),
                        make_nav_item("endpoints", "🔌", "模型端点"),
                        make_nav_item("playground", "🧠", "MoA 编排"),
                        make_nav_item("benchmark", "📈", "Benchmark"),
                        make_nav_item("prompts", "📝", "Prompt 模板"),
                        make_nav_item("settings", "⚙️", "系统设置"),
                    ],
                    spacing=1,
                ),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Text("v1.2.0  ·  flet", size=10, color=DARK.text_dim),
                    padding=ft.padding.only(left=20, right=20, bottom=14, top=14),
                ),
            ],
        ),
        bgcolor=DARK.surface,
        width=240,
        border=ft.border.only(right=ft.BorderSide(1, DARK.border)),
    )

    # 主内容
    content_stack = ft.Column(
        controls=[ft.Container(content=ft.Text("⏳ 正在加载…"), padding=32)],
        expand=True,
    )

    # 主题选择器
    theme_picker = ft.Dropdown(
        value="auto",
        width=160,
        options=[ft.dropdown.Option(k, f"{v['icon']} {v['name']}") for k, v in THEMES.items()],
        on_change=lambda e: apply_theme(e.control.value),
        text_size=12,
    )

    def switch_page(key: str):
        state["current_page"] = key
        # 重建右侧内容
        if key in page_views:
            content_stack.controls = [page_views[key]]
        # 更新侧栏 active 状态
        for k, btn in nav_buttons.items():
            p = state["palette"]
            if k == key:
                btn.content.controls[0].color = p.accent
                btn.content.controls[1].color = p.accent
                btn.content.controls[1].weight = ft.FontWeight.BOLD
                btn.bgcolor = p.accent_dim
            else:
                btn.content.controls[0].color = p.text_sec
                btn.content.controls[1].color = p.text_sec
                btn.content.controls[1].weight = ft.FontWeight.W_500
                btn.bgcolor = None
        page.update()

    def rebuild_ui():
        """主题切换后重建"""
        p = state["palette"]
        # 更新所有现有组件颜色
        status_bar.bgcolor = p.surface
        status_bar.border = ft.border.only(bottom=ft.BorderSide(1, p.border))
        status_text.color = p.text_dim
        start_btn.bgcolor = p.accent
        sidebar.bgcolor = p.surface
        sidebar.border = ft.border.only(right=ft.BorderSide(1, p.border))
        # 当前页面重建
        if state["current_page"] in page_views:
            page_views[state["current_page"]] = build_pages[state["current_page"]](
                state, server_runner
            )
            content_stack.controls = [page_views[state["current_page"]]]
        # 侧栏 active
        for k, btn in nav_buttons.items():
            if k == state["current_page"]:
                btn.bgcolor = p.accent_dim
            else:
                btn.bgcolor = None
        # 状态栏 dot 颜色
        status_dot.bgcolor = p.success if server_runner.is_running else p.text_dim
        # 时钟
        _update_time()

    # 时钟
    def _update_time():
        status_time.value = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        status_time.color = state["palette"].text_sec
        status_time.update()

    # 状态更新
    def update_status():
        p = state["palette"]
        if server_runner.is_running:
            status_dot.bgcolor = p.success
            status_text.value = f"运行中  ·  :{server_runner.port}"
            start_btn.text = "⏹ 停止"
        else:
            status_dot.bgcolor = p.text_dim
            status_text.value = "未启动"
            start_btn.text = "▶ 启动服务"
        status_text.color = p.text_dim
        start_btn.bgcolor = p.accent
        page.update()

    def toggle_server():
        if server_runner.is_running:
            server_runner.stop()
        else:
            server_runner.start()
        update_status()
        # 通知当前 page 刷新
        if state["current_page"] in page_views:
            page_views[state["current_page"]] = build_pages[state["current_page"]](
                state, server_runner
            )
            content_stack.controls = [page_views[state["current_page"]]]
            page.update()

    # ========== 页面构造 ==========
    build_pages = {
        "dashboard": build_dashboard,
        "endpoints": build_endpoints,
        "playground": build_playground,
        "benchmark": build_benchmark,
        "prompts": build_prompts,
        "settings": lambda s, sr: build_settings(s, sr, apply_theme, toggle_server, server_runner),
    }

    # 初始构建所有页面
    for key in build_pages:
        page_views[key] = build_pages[key](state, server_runner)

    # 默认页面
    content_stack.controls = [page_views["dashboard"]]

    # 时钟定时器
    import threading

    def clock_tick():
        while True:
            with contextlib.suppress(Exception):
                _update_time()
            import time

            time.sleep(1)

    threading.Thread(target=clock_tick, daemon=True).start()

    # 整体布局
    page.add(
        ft.Column(
            controls=[
                status_bar,
                ft.Row(
                    controls=[sidebar, content_stack],
                    spacing=0,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )
    )

    # 应用主题
    apply_theme("auto", update_picker=False)
    switch_page("dashboard")
    page.update()


def run():
    """启动 flet app"""
    ft.app(target=main, assets_dir=None)


if __name__ == "__main__":
    run()
