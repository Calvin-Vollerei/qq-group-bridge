"""界面配色：**单套深色主题**。

设计决定（按用户反馈收敛）：

* **只保留一套深色主题** —— 用户明确说"主题不要了"。原来的日夜双主题带来两个问题：
  日间主题在毛玻璃上文字对比度差；且切换主题要重建整套 QSS（有性能成本）。
  收敛成一套后，对比度只需调好一次，也没有了切换开销。
* **对比度优先**：卡片底色足够实（保证文字清晰），又不至于把窗口模糊完全盖住。
  毛玻璃的"透"体现在卡片之外的空隙与整体调性上。
* **单一数据源**：所有颜色集中在这里，QSS 由 ``build_qss`` 生成。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    """配色（单套深色）。"""

    # 窗口与层级
    window_tint: str      # 顶层半透明底（不盖死毛玻璃，也保证不是死黑）
    card: str
    card_hover: str
    card_strong: str
    border: str
    separator: str

    # 文字（对比度优先）
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


#: 唯一的一套主题。
#:
#: 底色选取依据：卡片 0.62 不透明度 —— 在亮/暗桌面背景下文字都能读清
#: （0.26 太透，白字压在亮桌面上会糊；0.86 又接近实心，毛玻璃看不出来）。
PALETTE = Palette(
    # 顶层：0.34 —— 足以让模糊透出来，又不会让内容"浮在虚空里"，
    # 也避免旧版那种"整窗死黑"
    window_tint="rgba(16, 18, 24, 0.34)",
    card="rgba(38, 42, 52, 0.62)",
    card_hover="rgba(52, 58, 70, 0.70)",
    card_strong="rgba(24, 27, 34, 0.86)",
    border="rgba(255, 255, 255, 0.16)",
    separator="rgba(255, 255, 255, 0.10)",

    # 文字：亮灰偏白，配 0.62 的卡片底可达 WCAG AA 级对比
    text="#F2F4F8",
    muted="#C3CBDA",
    faint="#9AA3B4",

    accent="#63A4FF",
    accent_hover="#82B8FF",
    accent_text="#0A1020",
    success="#5ADB8B",
    warning="#F5BC4F",
    danger="#FF7B7B",

    input_bg="rgba(20, 23, 30, 0.72)",
    scroll="rgba(255, 255, 255, 0.22)",
    selection="rgba(99, 164, 255, 0.38)",
)


def get() -> Palette:
    """取配色（单主题，保留函数是为了调用方不必改）。"""
    return PALETTE


#: 兼容旧调用：主题概念已删除，这里只返回一套。
THEMES = {"dark": PALETTE}
DEFAULT_THEME = "dark"


def build_qss(p: Palette | None = None) -> str:
    """生成全局样式表。单主题，不再有 frameless 开关。

    ⚠️ 顶层（QWidget#Root）**不设 background** —— 窗口背景由
    ``blur.py`` 的原生模糊负责，任何实底都会把它盖住
    （实测踩过：客户区变成死黑，只有系统标题栏那条是模糊的）。
    这里只给子控件上样式，并把 Card 之外的容器显式透明化。
    """
    p = p or PALETTE
    return f"""
    QMainWindow, QDialog, QWidget, QStackedWidget, QTabWidget {{
        background: transparent;
    }}
    QScrollArea, QAbstractScrollArea,
    QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QAbstractItemView, QTreeView, QListView, QTableView {{
        background: transparent;
    }}

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
    QFrame#TopBar {{
        /* 顶层条：用窗口底色而不是纯黑/纯白，避免"死黑或死白" */
        background: {p.card_strong};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QFrame#Sep {{ background: {p.separator}; max-height: 1px; border: none; }}

    QLabel {{ background: transparent; color: {p.text}; }}
    QLabel#Title {{ font-size: 15px; font-weight: 600; color: {p.text}; }}
    QLabel#Subtitle {{ font-size: 14px; font-weight: 600; color: {p.text}; }}
    QLabel#Muted {{ color: {p.muted}; }}
    QLabel#Hint {{ color: {p.faint}; font-size: 12px; }}
    QLabel#Success {{ color: {p.success}; }}
    QLabel#Warning {{ color: {p.warning}; }}
    QLabel#Danger {{ color: {p.danger}; }}
    QLabel#Metric {{ font-size: 20px; font-weight: 600; color: {p.text}; }}

    QPushButton {{
        color: {p.text};
        background: {p.card_hover};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {p.accent}; color: {p.accent_text}; }}
    QPushButton:pressed {{ background: {p.accent_hover}; }}
    QPushButton:disabled {{ color: {p.faint}; background: transparent; }}
    QPushButton#Primary {{
        background: {p.accent}; color: {p.accent_text};
        border: 1px solid {p.accent}; font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {p.accent_hover}; }}
    QPushButton#Ghost {{ background: transparent; }}
    QPushButton#Ghost:hover {{ background: {p.card_hover}; color: {p.text}; }}
    QPushButton#Danger {{ background: transparent; color: {p.danger};
                          border: 1px solid {p.danger}; }}
    QPushButton:checked {{ background: {p.accent}; color: {p.accent_text};
                           border: 1px solid {p.accent}; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.input_bg};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 6px 8px;
        selection-background-color: {p.selection};
        selection-color: #FFFFFF;
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
        background: transparent;
    }}
    QTabBar::tab {{
        color: {p.muted}; background: {p.card};
        padding: 8px 16px; margin-right: 4px;
        border: 1px solid {p.border};
        border-radius: 9px;
    }}
    QTabBar::tab:hover {{ color: {p.text}; background: {p.card_hover}; }}
    QTabBar::tab:selected {{
        color: {p.accent_text}; background: {p.accent}; font-weight: 600;
    }}

    QListWidget, QTreeView, QTableView {{
        background: {p.input_bg};
        alternate-background-color: {p.separator};
        border: 1px solid {p.border};
        border-radius: 10px;
        color: {p.text};
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}
    QHeaderView::section {{
        background: {p.card_hover}; color: {p.text};
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
        background: {p.input_bg}; border: none; border-radius: 6px;
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
    """
