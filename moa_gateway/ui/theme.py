"""moa_gateway.ui.theme — flet 主题(Light / Dark / Auto)

iOS 风格 · 玻璃拟态 · 圆角 · 弹性动画
"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft


# ========== 主题色板 ==========
@dataclass
class Palette:
    bg: str
    surface: str
    surface_2: str
    card: str
    bg_tertiary: str
    bg_elevated: str
    border: str
    border_strong: str
    text: str
    text_sec: str
    text_dim: str
    accent: str
    accent_hover: str
    accent_dim: str
    success: str
    warning: str
    danger: str
    gradient: str


DARK = Palette(
    bg="#0a0a0c",
    surface="#16161a",
    surface_2="#1f1f24",
    card="#1a1a1f",
    bg_tertiary="#1f1f24",
    bg_elevated="#22222a",
    border="rgba(255,255,255,0.08)",
    border_strong="rgba(255,255,255,0.16)",
    text="#f5f5f7",
    text_sec="#a0a0a8",
    text_dim="#6e6e78",
    accent="#5e9cff",
    accent_hover="#7eb1ff",
    accent_dim="rgba(94,156,255,0.16)",
    success="#34c759",
    warning="#ff9f0a",
    danger="#ff453a",
    gradient="#5e9cff",
)

LIGHT = Palette(
    bg="#f5f5f7",
    surface="#ffffff",
    surface_2="#fafafc",
    card="#ffffff",
    bg_tertiary="#f0f0f4",
    bg_elevated="#ffffff",
    border="rgba(0,0,0,0.08)",
    border_strong="rgba(0,0,0,0.16)",
    text="#1c1c1e",
    text_sec="#5a5a60",
    text_dim="#8e8e93",
    accent="#0a84ff",
    accent_hover="#0070e0",
    accent_dim="rgba(10,132,255,0.10)",
    success="#34c759",
    warning="#ff9f0a",
    danger="#ff3b30",
    gradient="#0a84ff",
)

THEMES = {
    "auto": {"name": "跟随系统", "icon": "🌓", "desc": "自动切换深浅色"},
    "light": {"name": "浅色", "icon": "☀️", "desc": "明亮背景,适合白天使用"},
    "dark": {"name": "深色", "icon": "🌙", "desc": "深色背景,护眼省电"},
}


def get_palette(theme: str) -> Palette:
    """获取当前主题色板(theme = light / dark / auto)"""
    if theme == "auto":
        # 跟随系统 — flet 会传过来
        return DARK  # 默认 dark,被 main 覆盖
    if theme == "light":
        return LIGHT
    return DARK


def make_flet_theme(p: Palette, mode: ft.ThemeMode) -> ft.Theme:
    """构造 flet Theme 对象"""
    return ft.Theme(
        color_scheme_seed=p.accent,
        color_scheme=ft.ColorScheme(
            primary=p.accent,
            on_primary="#ffffff",
            secondary=p.accent,
            surface=p.surface,
            on_surface=p.text,
            error=p.danger,
        ),
        use_material3=True,
        visual_density=ft.VisualDensity.STANDARD,
    )


def make_dark_theme(p: Palette = DARK) -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=p.accent,
        color_scheme=ft.ColorScheme(
            primary=p.accent,
            on_primary="#ffffff",
            secondary=p.accent,
            surface=p.surface,
            on_surface=p.text,
            error=p.danger,
        ),
        use_material3=True,
    )
