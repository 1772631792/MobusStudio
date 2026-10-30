"""VOFA+-inspired light and dark visual systems."""

LIGHT_STYLE = r"""
* { outline: none; }
QMainWindow, QWidget { background: #eef1f5; color: #253247; }
QToolTip { background: rgba(38, 54, 74, 238); color: #ffffff; border: none; border-radius: 7px; padding: 7px 9px; }
QFrame#topBar, QFrame#statusCard, QFrame#panel, QFrame#composerCard {
    background: rgba(255, 255, 255, 235); border: 1px solid rgba(207, 217, 228, 220); border-radius: 13px;
}
QLabel#brand { color: #056cb5; font-size: 20px; font-weight: 750; }
QLabel#muted { color: #7d8998; }
QLabel#sectionTitle { color: #086fb9; font-size: 15px; font-weight: 700; }
QLabel#metric { color: #172a41; font-size: 23px; font-weight: 700; }
QFrame#pollStatusCard { background: #e7f4f8; border: 1px solid #d1e9f1; border-radius: 10px; }
QLabel#pollValue { color: #187195; font-family: 'Cascadia Mono', Consolas; font-weight: 650; }
QPushButton { background: rgba(255, 255, 255, 225); border: 1px solid #ccd5df; border-radius: 9px; padding: 8px 14px; min-height: 22px; color: #354359; }
QPushButton:hover { background: #f3f8fc; border-color: #81afd0; color: #056cb5; }
QPushButton:pressed { background: #e5f0f8; }
QPushButton:checked { background: #e6f4fb; border-color: #58a5d5; color: #056cae; }
QPushButton#primary { background: #0879be; border-color: #0879be; color: #ffffff; font-weight: 700; }
QPushButton#primary:hover { background: #1593d5; }
QPushButton#danger { color: #e35a68; }
QPushButton:disabled { color: #a6aeb9; background: #f1f3f6; border-color: #dfe4e9; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: rgba(255, 255, 255, 230); border: 1px solid #cbd4de; border-radius: 9px; padding: 7px 10px; min-height: 22px; selection-background-color: #1687c7; }
QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #6faad0; }
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 8px; color: #526174; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #9aa9ba; border-radius: 4px; background: #ffffff; }
QCheckBox::indicator:checked { background: #08b9a1; border-color: #08b9a1; }
QTabWidget::pane { border: 1px solid #d7dee7; background: rgba(255, 255, 255, 225); border-radius: 12px; top: -1px; }
QTabBar::tab { background: rgba(232, 237, 242, 220); color: #657387; padding: 11px 15px; min-width: 50px; border: 1px solid #d7dee7; border-bottom: 2px solid transparent; }
QTabBar::tab:first { border-top-left-radius: 10px; }
QTabBar::tab:last { border-top-right-radius: 10px; }
QTabBar::tab:selected { color: #0877bb; background: rgba(255, 255, 255, 240); border-bottom: 2px solid #08b9a1; font-weight: 700; }
QTableView { background: #ffffff; alternate-background-color: #f7f9fb; border: none; gridline-color: #e6eaf0; selection-background-color: #dceff8; selection-color: #174d6c; }
QHeaderView::section { background: #f1f4f7; color: #5f6c7d; border: none; border-bottom: 1px solid #dce2e9; padding: 8px; font-weight: 650; }
QPlainTextEdit, QTextEdit { background: #ffffff; border: none; color: #344459; selection-background-color: #bee3f3; font-family: Consolas, 'Cascadia Mono'; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #c3ccd6; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #dce2e9; width: 2px; height: 2px; }
QStatusBar { background: #e4e9ef; color: #6b7788; }
QMenu { background: #ffffff; border: 1px solid #c7d1dc; border-radius: 12px; padding: 8px; font-size: 15px; }
QMenu::item { padding: 9px 28px 9px 14px; border-radius: 8px; margin: 1px 0; }
QMenu::item:selected { background: #dff2fa; color: #066cae; }
QMenu::separator { height: 1px; background: rgba(189, 201, 214, 150); margin: 6px 10px; }
"""


DARK_STYLE = r"""
* { outline: none; }
QMainWindow, QWidget { background: #0c111b; color: #dce5f2; }
QToolTip { background: rgba(32, 42, 58, 238); color: #eef5ff; border: 1px solid #384962; border-radius: 7px; padding: 7px 9px; }
QFrame#topBar, QFrame#statusCard, QFrame#panel, QFrame#composerCard {
    background: rgba(18, 26, 39, 235); border: 1px solid rgba(43, 59, 80, 220); border-radius: 13px;
}
QLabel#brand { color: #f4f8ff; font-size: 20px; font-weight: 700; }
QLabel#muted { color: #78879b; }
QLabel#sectionTitle { color: #f2f6fb; font-size: 15px; font-weight: 650; }
QLabel#metric { color: #f7fbff; font-size: 23px; font-weight: 700; }
QFrame#pollStatusCard { background: #10252d; border: 1px solid #1c3c47; border-radius: 10px; }
QLabel#pollValue { color: #70d5e8; font-family: 'Cascadia Mono', Consolas; font-weight: 650; }
QPushButton { background: rgba(25, 35, 52, 225); border: 1px solid #2a384d; border-radius: 9px; padding: 8px 14px; min-height: 22px; color: #dbe6f4; }
QPushButton:hover { background: #223048; border-color: #3e526d; }
QPushButton:pressed { background: #152032; }
QPushButton#primary { background: #00bfa5; border-color: #00bfa5; color: #061b18; font-weight: 700; }
QPushButton#primary:hover { background: #1bd7bc; }
QPushButton#danger { color: #ff7e8d; }
QPushButton:disabled { color: #566277; background: #141b27; border-color: #202a39; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: rgba(13, 20, 32, 230); border: 1px solid #29374b; border-radius: 9px; padding: 7px 10px; min-height: 22px; selection-background-color: #00a58f; }
QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #3c506b; }
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 8px; color: #adbacb; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #40516a; border-radius: 4px; background: #0d1420; }
QCheckBox::indicator:checked { background: #00bfa5; border-color: #00bfa5; }
QTabWidget::pane { border: 1px solid #202d3f; background: rgba(16, 24, 36, 225); border-radius: 12px; top: -1px; }
QTabBar::tab { background: rgba(16, 23, 35, 220); color: #8290a3; padding: 11px 15px; min-width: 50px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #23d5bc; background: rgba(22, 34, 50, 240); border-bottom: 2px solid #23d5bc; }
QTableView { background: #0e1520; alternate-background-color: #111b29; border: none; gridline-color: #1b2636; selection-background-color: #183b42; selection-color: #eafffb; }
QHeaderView::section { background: #141e2c; color: #8190a5; border: none; border-bottom: 1px solid #233146; padding: 8px; font-weight: 600; }
QPlainTextEdit, QTextEdit { background: #0a1019; border: none; color: #b8c8db; selection-background-color: #155e56; font-family: Consolas, 'Cascadia Mono'; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #2c394b; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #151f2d; width: 2px; height: 2px; }
QStatusBar { background: #0a0f17; color: #75849a; }
QMenu { background: rgba(22, 32, 47, 238); border: 1px solid rgba(64, 82, 108, 210); border-radius: 12px; padding: 8px; font-size: 15px; }
QMenu::item { padding: 9px 28px 9px 14px; border-radius: 8px; margin: 1px 0; }
QMenu::item:selected { background: rgba(39, 59, 84, 225); }
QMenu::separator { height: 1px; background: rgba(79, 97, 122, 150); margin: 6px 10px; }
"""

# Backward-compatible default. The application starts in VOFA+-style light mode.
APP_STYLE = LIGHT_STYLE
