"""PySide6 界面的主题系统：日夜两套配色 + 全局 QSS。

设计要点：

* **单一数据源**：所有颜色集中在 ``THEMES`` 里，QSS 由它生成 ——
  改一处即全局生效，不会出现"某个控件忘了改颜色"。
* **可持久化**：主题 key（``dark`` / ``light``）直接存进配置，下次启动保持。
* 颜色用 Qt 能直接吃的字符串，透明度用 ``rgba(...)`` 表达。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    """一套配色。"""

    key: str
    name: str

    # 窗口与卡片
    window_tint: str
    card: str
    card_hover: str
    card_strong: str
    border: str
    separator: str

    # 文字
    text: str
    muted: str
    faint: str

    # 强调与语义
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    warning: str
    danger: str

    # 控件
    input_bg: str
    scroll: str
    selection: str

    #: 原生材质/DWM 的明暗开关
    is_dark: bool


DARK = Palette(
    key="dark", name="夜间",
    window_tint="rgba(18, 20, 26, 0.22)",
    card="rgba(58, 62, 74, 0.26)",
    card_hover="rgba(74, 80, 94, 0.36)",
    card_strong="rgba(30, 33, 41, 0.52)",
    border="rgba(255, 255, 255, 0.14)",
    separator="rgba(255, 255, 255, 0.08)",
    text="#EAEDF4", muted="#A3ACBC", faint="#6E7787",
    accent="#5B9BFF", accent_hover="#79AEFF", accent_text="#0B1220",
    success="#4ED17E", warning="#F2B440", danger="#FF6B6B",
    input_bg="rgba(26, 29, 36, 0.55)",
    scroll="rgba(255, 255, 255, 0.18)",
    selection="rgba(91, 155, 255, 0.35)",
    is_dark=True,
)

LIGHT = Palette(
    key="light", name="日间",
    window_tint="rgba(248, 250, 253, 0.22)",
    card="rgba(255, 255, 255, 0.30)",
    card_hover="rgba(255, 255, 255, 0.46)",
    card_strong="rgba(252, 253, 255, 0.58)",
    border="rgba(0, 0, 0, 0.10)",
    separator="rgba(0, 0, 0, 0.06)",
    text="#1A1E26", muted="#5A6472", faint="#8A93A1",
    accent="#2F6FEB", accent_hover="#4A83F0", accent_text="#FFFFFF",
    success="#1F9D55", warning="#B7791F", danger="#D64545",
    input_bg="rgba(255, 255, 255, 0.66)",
    scroll="rgba(0, 0, 0, 0.18)",
    selection="rgba(47, 111, 235, 0.22)",
    is_dark=False,
)

THEMES: dict[str, Palette] = {"dark": DARK, "light": LIGHT}
DEFAULT_THEME = "dark"


def get(key: str | None) -> Palette:
    return THEMES.get((key or "").lower(), THEMES[DEFAULT_THEME])


def flipped(key: str | None) -> str:
    """返回另一个主题的 key。"""
    return "light" if (key or "").lower() == "dark" else "dark"


def build_qss(p: Palette, *, frameless: bool = True) -> str:
    """由配色生成全局样式表。

    ``frameless=True``（默认，毛玻璃窗口）时窗口自绘圆角底；
    否则只给子控件上样式，边框交给系统。
    """
    root = ""
    if frameless:
        root = f"""
        QWidget#Root {{
            background: {p.window_tint};
            border: 1px solid {p.border};
            border-radius: 16px;
        }}
        """
    return root + f"""
    QWidget {{ color: {p.text}; font-size: 13px; }}

    QFrame#Card {{
        background: {p.card};
        border: 1px solid {p.border};
        border-radius: 14px;
    }}
    QFrame#CardStrong {{
        background: {p.card_strong};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QFrame#Sep {{ background: {p.separator}; max-height: 1px; border: none; }}

    QLabel {{ background: transparent; color: {p.text}; }}
    QLabel#Title {{ font-size: 16px; font-weight: 600; }}
    QLabel#Subtitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#Muted {{ color: {p.muted}; }}
    QLabel#Hint {{ color: {p.faint}; font-size: 12px; }}
    QLabel#Success {{ color: {p.success}; }}
    QLabel#Warning {{ color: {p.warning}; }}
    QLabel#Danger {{ color: {p.danger}; }}
    QLabel#Metric {{ font-size: 20px; font-weight: 600; }}

    QPushButton {{
        color: {p.text};
        background: {p.card};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {p.card_hover}; }}
    QPushButton:pressed {{ background: {p.separator}; }}
    QPushButton:disabled {{ color: {p.faint}; background: transparent; }}
    QPushButton#Primary {{
        background: {p.accent}; color: {p.accent_text};
        border: 1px solid {p.accent}; font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {p.accent_hover}; }}
    QPushButton#Danger {{ background: transparent; color: {p.danger};
                          border: 1px solid {p.danger}; }}
    QPushButton:checked {{ background: {p.accent}; color: {p.accent_text};
                           border: 1px solid {p.accent}; }}
    QPushButton#Ghost {{ background: transparent; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.input_bg};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 6px 8px;
        selection-background-color: {p.selection};
        selection-color: {p.text};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {p.card_strong}; color: {p.text};
        border: 1px solid {p.border};
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}

    QTabWidget::pane {{
        border: 1px solid {p.border}; border-radius: 12px;
        background: {p.card}; top: -1px;
    }}
    QTabBar::tab {{
        color: {p.muted}; background: transparent;
        padding: 8px 16px; margin-right: 4px; border-radius: 9px;
    }}
    QTabBar::tab:hover {{ color: {p.text}; background: {p.separator}; }}
    QTabBar::tab:selected {{ color: {p.accent_text}; background: {p.accent};
                             font-weight: 600; }}

    QTreeView, QTableView, QListView {{
        background: {p.card};
        alternate-background-color: {p.separator};
        border: 1px solid {p.border};
        border-radius: 10px;
        selection-background-color: {p.selection};
        selection-color: {p.text};
        gridline-color: {p.separator};
    }}
    QHeaderView::section {{
        background: {p.card_hover}; color: {p.muted};
        border: none; border-bottom: 1px solid {p.border};
        padding: 6px 8px;
    }}
    QCheckBox, QRadioButton {{ background: transparent; color: {p.text}; spacing: 7px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 15px; height: 15px;
        border: 1px solid {p.border}; background: {p.input_bg};
    }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {p.accent}; border-color: {p.accent};
    }}

    QProgressBar {{
        background: {p.separator}; border: none; border-radius: 6px;
        height: 12px; text-align: center; color: {p.muted};
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 6px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {p.scroll}; border-radius: 5px; min-height: 26px; }}
    QScrollBar::handle:hover {{ background: {p.accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent; border: none; height: 0; width: 0;
    }}

    QToolTip {{
        background: {p.card_strong}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 8px; padding: 6px 8px;
    }}
    QMenu {{
        background: {p.card_strong}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 10px; padding: 6px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
    QSplitter::handle {{ background: {p.separator}; }}

    /* ================= 关键：禁止"实底"，否则会盖住 DWM 材质 =================
       用户实测现象：**只有最顶部标题栏那条是模糊的，主页面还是黑的**。
       原因就是下面这些控件在部分平台会画自己的不透明底，
       而 DWM 的亚克力画在窗口**背后** —— 被盖住就彻底看不到了。
       必须显式声明透明，让材质透上来。 ================= */
    QMainWindow, QDialog, QWidget#Root, QTabWidget, QStackedWidget {{
        background: transparent;
    }}
    QScrollArea, QAbstractScrollArea,
    QScrollArea > QWidget > QWidget,
    QScrollArea > QWidget > QViewport {{
        background: transparent;
        border: none;
    }}
    QAbstractItemView, QTreeView, QListView, QTableView, QHeaderView {{
        background: transparent;
    }}
    """
