"""EtherScope 2.3.0 workbench style adapted to DSP Studio's audio interface."""
import json
from functools import lru_cache
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPalette, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


@lru_cache(maxsize=64)
def outline_icon(name, color, disabled_color):
    """Small native SVG icons, rendered at 4x for fractional display scaling."""
    paths = {
        "play": '<path d="m8 4 11 8-11 8Z" fill="currentColor"/>',
        "stop": '<rect x="6" y="6" width="12" height="12" fill="currentColor"/>',
        "waveform": '<path d="M2 12h4l2-8 4 16 4-12 2 4h4"/>',
        "download": '<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
        "moon": '<path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z"/>',
        "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 .5c0 1.5-2.5 1.5-2.5 3M12 17h.01"/>',
        "users": '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m2-15a3 3 0 0 1 0 6m4 9v-2a6 6 0 0 0-3-5"/>',
        "activity": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
        "noise": '<path d="M4 9v6m4-10v14m4-11v8m4-13v18m4-11v4"/>',
        "folder": '<path d="M3 8V5h6l2 3h10l-3 11H4L2 8Z"/>',
        "reset": '<path d="M4 9a8 8 0 1 1 0 7M4 3v6h6"/>',
        "mic": '<rect x="9" y="3" width="6" height="12"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
    }
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, disabled_color)):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{ink}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'.replace("currentColor", ink)
        pixmap = QPixmap(64, 64)
        pixmap.setDevicePixelRatio(4)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(svg.encode()).render(painter, QRectF(0, 0, 16, 16))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def apply_theme(root, theme="light"):
    generated = root / "ui" / "generated-etherscope-v3"
    tokens = json.loads((generated / "tokens.json").read_text(encoding="utf-8"))
    c = tokens["colors"][theme]
    contract = json.loads((generated / "project-contract.json").read_text(encoding="utf-8"))
    tokens["presentation"] = contract["presentation"]
    c.update(contract["presentation"]["semantic_colors"][theme])
    palette = QPalette()
    roles = {
        "Window": "workspace", "WindowText": "text", "Base": "surface",
        "AlternateBase": "alternate", "Text": "text", "Button": "bar",
        "ButtonText": "text", "Highlight": "selection", "HighlightedText": "text",
        "PlaceholderText": "muted", "Mid": "border", "ToolTipBase": "bar",
        "ToolTipText": "text",
    }
    for role, key in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(c[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    extra = """
    QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; }
    QMainWindow#mainWindow { background: @workspace; border: 1px solid @border; }
    QMainWindow#mainWindow[frameMaximized="true"] { border: none; }
    QWidget#windowTitleBar { background: @workspace; }
    QLabel#windowTitle { color: @text; font-weight: 600; }
    QFrame#toolbarBar { background: @bar; border-bottom: 1px solid @border; }
    QFrame[wbRole="surface"], QDialog { background: @workspace; }
    QLabel[wbRole="muted"] { color: @muted; }
    QLabel[wbRole="section"] { color: @text; background: transparent; border-bottom: 1px solid @border; padding: 0 0 2px; font-weight: 500; }
    QLabel[wbRole="data"] { font-family: 'Consolas'; font-size: 13px; }
    QLabel#speaker { font-size: 13px; font-weight: 600; color: @text; }
    QLabel#emptyPlot { color: @muted; background: transparent; font-size: 13px; padding: 12px; }
    QLabel#statusBadge { color: @muted; background: transparent; border: none; padding: 0 4px; font-size: 13px; }
    QLabel#statusBadge[wbTone="success"] { color: @success; }
    QLabel#statusBadge[wbTone="warning"] { color: @warning; }
    QLabel#statusBadge[wbTone="error"] { color: @error; }
    QPushButton, QToolButton { min-height: 28px; padding: 0 8px; border: 1px solid transparent; border-radius: @cornerpx; background: transparent; }
    QPushButton:hover, QToolButton:hover { background: @hover; }
    QPushButton:pressed, QToolButton:pressed { background: @selection; border-color: @accent; }
    QPushButton:focus, QToolButton:focus { border-color: @accent; }
    QPushButton[wbRole="primary"] { background: @primary; color: @primary_text; font-weight: 600; }
    QPushButton[wbRole="primary"]:hover { background: @primary_hover; }
    QPushButton[wbRole="primary"]:pressed { background: @primary_pressed; }
    QPushButton[wbRole="primary"]:disabled { background: @hover; color: @disabled; }
    QPushButton[wbRole="primary"][wbRunning="true"] { background: transparent; color: @error; border-color: @error; }
    QPushButton[wbRole="primary"][wbRunning="true"]:hover { background: @hover; }
    QPushButton[wbRole="primary"][wbRunning="true"]:pressed { background: @selection; }
    QPushButton[wbRole="secondary"], QPushButton[wbRole="ghost"] { background: transparent; border-color: transparent; color: @text; }
    QPushButton[wbRole="secondary"] { text-align: left; }
    QPushButton[wbRole="secondary"]:hover, QPushButton[wbRole="ghost"]:hover { background: @hover; }
    QPushButton[wbRole="secondary"]:focus, QPushButton[wbRole="ghost"]:focus { border-color: @accent; }
    QPushButton[wbRole="secondary"]:pressed, QPushButton[wbRole="ghost"]:pressed { background: @selection; border-color: @accent; }
    QPushButton:disabled, QPushButton[wbRole="secondary"]:disabled, QPushButton[wbRole="ghost"]:disabled { color: @disabled; }
    QTabWidget::pane { border: none; background: @workspace; }
    QTabBar { background: @bar; border: none; }
    QTabBar::tab { min-width: 36px; min-height: 22px; padding: 4px 8px; margin: 0; border: none; border-bottom: 2px solid transparent; background: transparent; color: @muted; }
    QTabBar::tab:selected { background: transparent; color: @text; border-bottom-color: @accent; font-weight: 400; }
    QTabBar::tab:hover { background: @hover; }
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { min-height: 28px; padding: 0 6px; border: 1px solid @border; border-radius: @cornerpx; background: @surface; }
    QDoubleSpinBox, QSpinBox { font-family: 'Consolas'; }
    QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QComboBox:focus { border-color: @accent; }
    QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled { color: @disabled; border-color: @border; }
    QCheckBox { spacing: 5px; min-height: 24px; color: @muted; }
    QCheckBox:focus { color: @text; outline: 1px solid @accent; }
    QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid @muted; border-radius: @cornerpx; background: @surface; }
    QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url('@check_image'); }
    QCheckBox::indicator:disabled { border-color: @border; }
    QCheckBox:disabled { color: @disabled; }
    QScrollArea { border: none; background: @surface; }
    QScrollArea > QWidget > QWidget { background: @surface; }
    QTableView { alternate-background-color: @alternate; gridline-color: @border; border: 1px solid transparent; }
    QTableView:focus { border-color: @accent; }
    QTableView::item { padding: 2px 5px; }
    QTableView::item:selected { background: @selection; color: @text; }
    QHeaderView::section { font-weight: 400; font-size: 13px; padding: 3px 7px; background: @bar; border: none; border-bottom: 1px solid @border; color: @muted; }
    QFrame#speakerPanel { border: none; border-top: 1px solid @border; background: @workspace; }
    QFrame#outputBar { border: none; border-top: 1px solid @border; background: @workspace; }
    QPushButton#detailsButton { color: @muted; }
    QComboBox::drop-down { border: none; width: 18px; }
    QComboBox::down-arrow { image: url('@chevron_image'); width: 10px; height: 8px; }
    QDoubleSpinBox::down-arrow, QSpinBox::down-arrow { image: url('@chevron_image'); width: 9px; height: 7px; }
    QDoubleSpinBox::up-arrow, QSpinBox::up-arrow { image: url('@chevron_up_image'); width: 9px; height: 7px; }
    QDoubleSpinBox::up-button, QSpinBox::up-button { width: 18px; border: none; background: transparent; subcontrol-origin: border; subcontrol-position: top right; }
    QDoubleSpinBox::down-button, QSpinBox::down-button { width: 18px; border: none; background: transparent; subcontrol-origin: border; subcontrol-position: bottom right; }
    QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover { background: @hover; }
    QComboBox QAbstractItemView { background: @surface; border: 1px solid @border; selection-background-color: @selection; color: @text; padding: 3px; }
    QSplitter::handle { background: @workspace; }
    QSplitter::handle:horizontal { border-left: 1px solid @border; }
    QSplitter::handle:hover { background: @selection; }
    QScrollBar:vertical { background: @surface; width: 8px; margin: 0; }
    QScrollBar:horizontal { background: @surface; height: 8px; margin: 0; }
    QScrollBar::handle { background: @border; min-height: 24px; min-width: 24px; }
    QScrollBar::handle:hover { background: @muted; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QSlider::groove:horizontal { height: 3px; background: @border; }
    QSlider::sub-page:horizontal { background: @accent; }
    QSlider::handle:horizontal { width: 8px; margin: -5px 0; background: @accent; border: 1px solid @accent; }
    QSlider::handle:horizontal:focus { border-color: @text; }
    """
    extra = extra.replace("@corner", str(contract["presentation"]["qt_corner_radius"]))
    check_icon = "check-dark.svg" if theme == "dark" else "check.svg"
    extra = extra.replace("@check_image", (root / "ui" / check_icon).as_posix())
    extra = extra.replace("@chevron_image", (root / "ui" / f"chevron-{theme}.svg").as_posix())
    extra = extra.replace("@chevron_up_image", (root / "ui" / f"chevron-up-{theme}.svg").as_posix())
    for key, value in sorted(c.items(), key=lambda item: -len(item[0])):
        extra = extra.replace("@" + key, value)
    app = QApplication.instance()
    font = QFont()
    font.setFamilies(tokens["fonts"]["ui"])
    font.setPixelSize(tokens["metrics"]["font_size"])
    app.setFont(font)
    app.setPalette(palette)
    app.setStyleSheet((generated / "qt" / f"theme-{theme}.qss").read_text(encoding="utf-8") + extra)
    return tokens, c
