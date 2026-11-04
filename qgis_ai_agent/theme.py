"""Theme manager and styling tokens for QGIS AI Agent.

Supports:
- Auto (Follows QGIS / system light or dark theme dynamically via QPalette)
- Explicit Light Theme
- Explicit Dark Theme
"""
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QPalette
from qgis.PyQt.QtWidgets import QApplication

SKEY_THEME = "qgis_ai_agent/theme"


def get_theme_mode():
    """Return user setting: 'auto' (default), 'light', or 'dark'."""
    s = QSettings()
    val = str(s.value(SKEY_THEME, "auto")).lower().strip()
    return val if val in ("auto", "light", "dark") else "auto"


def set_theme_mode(mode):
    """Save user theme preference: 'auto', 'light', or 'dark'."""
    if mode in ("auto", "light", "dark"):
        s = QSettings()
        s.setValue(SKEY_THEME, mode)


def is_dark():
    """Determine whether the active theme should be dark or light."""
    mode = get_theme_mode()
    if mode == "dark":
        return True
    if mode == "light":
        return False
    # Auto mode: inspect Qt Application palette window/base lightness
    try:
        app = QApplication.instance()
        if app:
            pal = app.palette()
            color_role = getattr(QPalette.ColorRole, "Window", None) if hasattr(QPalette, "ColorRole") else getattr(QPalette, "Window", 10)
            win_color = pal.color(color_role)
            # If window color lightness is < 128, it is a dark theme
            return win_color.lightness() < 128
    except Exception:
        pass
    return False


def get_theme():
    """Return color and style tokens dictionary for the active theme."""
    dark = is_dark()
    if dark:
        return {
            "is_dark": True,
            # Chat browser
            "chat_bg": "#12131a",
            "chat_border": "#232533",
            "chat_text": "#e2e8f0",
            # Prompt input
            "prompt_bg": "#1c1c26",
            "prompt_border": "#333344",
            "prompt_text": "#f8fafc",
            "prompt_placeholder": "#64748b",
            # Buttons & combos
            "btn_bg": "#1e1e2a",
            "btn_border": "#333344",
            "btn_text": "#cbd5e1",
            "btn_hover_bg": "#28283a",
            "btn_hover_text": "#ffffff",
            "combo_bg": "#171822",
            "combo_border": "#28283a",
            "combo_text": "#f1f5f9",
            # Header labels & metadata
            "lbl_subtle": "#94a3b8",
            "meta_text": "#94a3b8",
            "time_text": "#64748b",
            # User card
            "user_card_bg": "#172033",
            "user_card_border": "#233352",
            "user_card_accent": "#3b82f6",
            "user_card_text": "#f8fafc",
            "user_badge_bg": "#2563eb",
            "user_badge_text": "#eff6ff",
            # Assistant card
            "assistant_card_bg": "#171822",
            "assistant_card_border": "#28283a",
            "assistant_card_text": "#e2e8f0",
            "agent_badge_bg": "#059669",
            "agent_badge_text": "#ecfdf5",
            # Tool cards
            "tool_out_bg": "#0d1117",
            "tool_out_border": "#21262d",
            "tool_out_text": "#c9d1d9",
            "tool_out_err_bg": "#1c1418",
            "tool_out_err_border": "#7f1d1d",
            "tool_out_err_text": "#fca5a5",
            "tool_running_python_bg": "#1e112a",
            "tool_running_python_border": "#6b21a8",
            "tool_running_python_text": "#c084fc",
            "tool_running_mcp_bg": "#0c2340",
            "tool_running_mcp_border": "#1e4976",
            "tool_running_mcp_text": "#38bdf8",
            "tool_timeline_line": "#334155",
            "tool_header_category": "#f8fafc",
            "tool_header_desc": "#cbd5e1",
            "tool_img_pill_bg": "#0f172a",
            "tool_img_pill_border": "#1e293b",
            "tool_img_pill_text": "#38bdf8",
            # Code blocks & inline code
            "code_header_bg": "#1e2230",
            "code_header_text": "#93c5fd",
            "code_pre_bg": "#0f131a",
            "code_pre_border": "#283040",
            "code_pre_text": "#e2e8f0",
            "inline_code_bg": "#1e2230",
            "inline_code_text": "#93c5fd",
            # Interactive chips & links
            "chip_bg": "#1e293b",
            "chip_border": "#334155",
            "chip_text": "#38bdf8",
            "link_text": "#60a5fa",
            "revert_link_text": "#f59e0b",
            # Tables
            "table_th_bg": "#1e293b",
            "table_th_text": "#38bdf8",
            "table_td_odd": "#141923",
            "table_td_even": "#0f131a",
            "table_border": "#283040",
            "table_text": "#cbd5e1",
            # Quotes & HR
            "blockquote_bg": "#151824",
            "blockquote_border": "#6366f1",
            "blockquote_text": "#94a3b8",
            "hr_border": "#282f3d",
            # Headings
            "heading_h1": "#38bdf8",
            "heading_h2": "#60a5fa",
            "heading_h3": "#f1f5f9",
            "body_p": "#e2e8f0",
            "bullet": "#38bdf8",
            # Indicator & queue
            "indicator_bg": "#172554",
            "indicator_border": "#1e40af",
            "indicator_text": "#7dd3fc",
            # Attachment pills
            "pill_bg": "#1e293b",
            "pill_border": "#334155",
            "pill_text": "#e2e8f0",
            "badge_meta_bg": "#1a1a26",
            "badge_meta_text": "#cbd5e1",
            # Thinking node
            "thinking_bg": "#141724",
            "thinking_border": "#262a40",
            "thinking_text": "#94a3b8",
        }
    else:
        return {
            "is_dark": False,
            # Chat browser (Crisp modern Light Theme)
            "chat_bg": "#ffffff",
            "chat_border": "#cbd5e1",
            "chat_text": "#1e293b",
            # Prompt input
            "prompt_bg": "#ffffff",
            "prompt_border": "#cbd5e1",
            "prompt_text": "#0f172a",
            "prompt_placeholder": "#94a3b8",
            # Buttons & combos
            "btn_bg": "#f8fafc",
            "btn_border": "#cbd5e1",
            "btn_text": "#334155",
            "btn_hover_bg": "#e2e8f0",
            "btn_hover_text": "#0f172a",
            "combo_bg": "#ffffff",
            "combo_border": "#cbd5e1",
            "combo_text": "#0f172a",
            # Header labels & metadata
            "lbl_subtle": "#64748b",
            "meta_text": "#64748b",
            "time_text": "#94a3b8",
            # User card (Soft blue tint card)
            "user_card_bg": "#f0f7ff",
            "user_card_border": "#bae6fd",
            "user_card_accent": "#2563eb",
            "user_card_text": "#0f172a",
            "user_badge_bg": "#2563eb",
            "user_badge_text": "#ffffff",
            # Assistant card (Clean white card with subtle border)
            "assistant_card_bg": "#f8fafc",
            "assistant_card_border": "#e2e8f0",
            "assistant_card_text": "#1e293b",
            "agent_badge_bg": "#059669",
            "agent_badge_text": "#ffffff",
            # Tool cards
            "tool_out_bg": "#f8fafc",
            "tool_out_border": "#e2e8f0",
            "tool_out_text": "#334155",
            "tool_out_err_bg": "#fef2f2",
            "tool_out_err_border": "#fca5a5",
            "tool_out_err_text": "#991b1b",
            "tool_running_python_bg": "#faf5ff",
            "tool_running_python_border": "#d8b4fe",
            "tool_running_python_text": "#7e22ce",
            "tool_running_mcp_bg": "#f0f9ff",
            "tool_running_mcp_border": "#bae6fd",
            "tool_running_mcp_text": "#0284c7",
            "tool_timeline_line": "#cbd5e1",
            "tool_header_category": "#0f172a",
            "tool_header_desc": "#475569",
            "tool_img_pill_bg": "#f1f5f9",
            "tool_img_pill_border": "#e2e8f0",
            "tool_img_pill_text": "#0284c7",
            # Code blocks & inline code
            "code_header_bg": "#f1f5f9",
            "code_header_text": "#1d4ed8",
            "code_pre_bg": "#f8fafc",
            "code_pre_border": "#e2e8f0",
            "code_pre_text": "#1e293b",
            "inline_code_bg": "#f1f5f9",
            "inline_code_text": "#1d4ed8",
            # Interactive chips & links
            "chip_bg": "#eff6ff",
            "chip_border": "#bfdbfe",
            "chip_text": "#1d4ed8",
            "link_text": "#2563eb",
            "revert_link_text": "#d97706",
            # Tables
            "table_th_bg": "#f1f5f9",
            "table_th_text": "#0369a1",
            "table_td_odd": "#ffffff",
            "table_td_even": "#f8fafc",
            "table_border": "#e2e8f0",
            "table_text": "#334155",
            # Quotes & HR
            "blockquote_bg": "#f8fafc",
            "blockquote_border": "#4f46e5",
            "blockquote_text": "#475569",
            "hr_border": "#e2e8f0",
            # Headings
            "heading_h1": "#0369a1",
            "heading_h2": "#1d4ed8",
            "heading_h3": "#0f172a",
            "body_p": "#1e293b",
            "bullet": "#0284c7",
            # Indicator & queue
            "indicator_bg": "#eff6ff",
            "indicator_border": "#93c5fd",
            "indicator_text": "#1e40af",
            # Attachment pills
            "pill_bg": "#f1f5f9",
            "pill_border": "#cbd5e1",
            "pill_text": "#1e293b",
            "badge_meta_bg": "#f1f5f9",
            "badge_meta_text": "#475569",
            # Thinking node
            "thinking_bg": "#f8fafc",
            "thinking_border": "#e2e8f0",
            "thinking_text": "#64748b",
        }

