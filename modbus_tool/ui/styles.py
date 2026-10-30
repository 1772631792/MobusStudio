"""VOFA+-inspired light and dark visual systems."""

LIGHT_STYLE = r"""
* { outline: none; }
QMainWindow, QWidget { background: #eef1f5; color: #253247; }
QToolTip { background: #26364a; color: #ffffff; border: none; padding: 5px; }
QFrame#topBar, QFrame#statusCard, QFrame#panel, QFrame#composerCard {
    background: #ffffff; border: 1px solid #d9e0e8; border-radius: 9px;
}
QLabel#brand { color: #056cb5; font-size: 18px; font-weight: 750; }
QLabel#muted { color: #7d8998; }
QLabel#sectionTitle { color: #086fb9; font-size: 13px; font-weight: 700; }
QLabel#metric { color: #172a41; font-size: 21px; font-weight: 700; }
QPushButton { background: #ffffff; border: 1px solid #ccd5df; border-radius: 6px; padding: 7px 12px; color: #354359; }
QPushButton:hover { background: #f3f8fc; border-color: #81afd0; color: #056cb5; }
QPushButton:pressed { background: #e5f0f8; }
QPushButton:checked { background: #e6f4fb; border-color: #58a5d5; color: #056cae; }
QPushButton#primary { background: #0879be; border-color: #0879be; color: #ffffff; font-weight: 700; }
QPushButton#primary:hover { background: #1593d5; }
QPushButton#danger { color: #e35a68; }
QPushButton:disabled { color: #a6aeb9; background: #f1f3f6; border-color: #dfe4e9; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #ffffff; border: 1px solid #cbd4de; border-radius: 6px; padding: 6px 8px; min-height: 20px; selection-background-color: #1687c7; }
QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #6faad0; }
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 8px; color: #526174; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #9aa9ba; border-radius: 4px; background: #ffffff; }
QCheckBox::indicator:checked { background: #08b9a1; border-color: #08b9a1; }
QTabWidget::pane { border: 1px solid #d7dee7; background: #ffffff; border-radius: 9px; top: -1px; }
QTabBar::tab { background: #e8edf2; color: #657387; padding: 10px 12px; min-width: 46px; border: 1px solid #d7dee7; border-bottom: 2px solid transparent; }
QTabBar::tab:first { border-top-left-radius: 7px; }
QTabBar::tab:last { border-top-right-radius: 7px; }
QTabBar::tab:selected { color: #0877bb; background: #ffffff; border-bottom: 2px solid #08b9a1; font-weight: 700; }
QTableView { background: #ffffff; alternate-background-color: #f7f9fb; border: none; gridline-color: #e6eaf0; selection-background-color: #dceff8; selection-color: #174d6c; }
QHeaderView::section { background: #f1f4f7; color: #5f6c7d; border: none; border-bottom: 1px solid #dce2e9; padding: 8px; font-weight: 650; }
QPlainTextEdit, QTextEdit { background: #ffffff; border: none; color: #344459; selection-background-color: #bee3f3; font-family: Consolas, 'Cascadia Mono'; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #c3ccd6; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #dce2e9; width: 2px; height: 2px; }
QStatusBar { background: #e4e9ef; color: #6b7788; }
QMenu { background: #ffffff; border: 1px solid #d1d9e2; padding: 5px; }
QMenu::item { padding: 7px 20px; border-radius: 4px; }
QMenu::item:selected { background: #e1f1f8; color: #066cae; }
"""


DARK_STYLE = r"""
* { outline: none; }
QMainWindow, QWidget { background: #0c111b; color: #dce5f2; }
QToolTip { background: #202a3a; color: #eef5ff; border: 1px solid #384962; padding: 5px; }
QFrame#topBar, QFrame#statusCard, QFrame#panel, QFrame#composerCard {
    background: #121a27; border: 1px solid #202d3f; border-radius: 10px;
}
QLabel#brand { color: #f4f8ff; font-size: 18px; font-weight: 700; }
QLabel#muted { color: #78879b; }
QLabel#sectionTitle { color: #f2f6fb; font-size: 13px; font-weight: 650; }
QLabel#metric { color: #f7fbff; font-size: 21px; font-weight: 700; }
QPushButton { background: #192334; border: 1px solid #2a384d; border-radius: 7px; padding: 7px 12px; color: #dbe6f4; }
QPushButton:hover { background: #223048; border-color: #3e526d; }
QPushButton:pressed { background: #152032; }
QPushButton#primary { background: #00bfa5; border-color: #00bfa5; color: #061b18; font-weight: 700; }
QPushButton#primary:hover { background: #1bd7bc; }
QPushButton#danger { color: #ff7e8d; }
QPushButton:disabled { color: #566277; background: #141b27; border-color: #202a39; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #0d1420; border: 1px solid #29374b; border-radius: 7px; padding: 6px 8px; min-height: 20px; selection-background-color: #00a58f; }
QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #3c506b; }
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 8px; color: #adbacb; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #40516a; border-radius: 4px; background: #0d1420; }
QCheckBox::indicator:checked { background: #00bfa5; border-color: #00bfa5; }
QTabWidget::pane { border: 1px solid #202d3f; background: #101824; border-radius: 10px; top: -1px; }
QTabBar::tab { background: #101723; color: #8290a3; padding: 10px 12px; min-width: 46px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #23d5bc; background: #162232; border-bottom: 2px solid #23d5bc; }
QTableView { background: #0e1520; alternate-background-color: #111b29; border: none; gridline-color: #1b2636; selection-background-color: #183b42; selection-color: #eafffb; }
QHeaderView::section { background: #141e2c; color: #8190a5; border: none; border-bottom: 1px solid #233146; padding: 8px; font-weight: 600; }
QPlainTextEdit, QTextEdit { background: #0a1019; border: none; color: #b8c8db; selection-background-color: #155e56; font-family: Consolas, 'Cascadia Mono'; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #2c394b; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #151f2d; width: 2px; height: 2px; }
QStatusBar { background: #0a0f17; color: #75849a; }
QMenu { background: #16202f; border: 1px solid #2a394e; padding: 5px; }
QMenu::item { padding: 7px 20px; border-radius: 4px; }
QMenu::item:selected { background: #203148; }
"""

# Backward-compatible default. The application starts in VOFA+-style light mode.
APP_STYLE = LIGHT_STYLE
