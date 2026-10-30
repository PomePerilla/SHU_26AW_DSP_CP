"""Compact Qt title bar and Windows frame, adapted from EtherScope 2.3.0.

Copyright (c) 2026 EtherScope contributors. MIT license; see
ui/reference-licenses/EtherScope-MIT.txt. DSP Studio uses its own waveform mark.
"""
import sys
from PySide6.QtCore import Qt, QEvent, QPoint, QRectF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QLabel, QAbstractButton, QMenu
from .theme import outline_icon


class CaptionButton(QAbstractButton):
    def __init__(self, kind, window, parent):
        super().__init__(parent)
        self.kind, self.host = kind, window
        self.setObjectName(f"window{kind.title()}Button")
        self.setFixedSize(44, 32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        colors = self.host.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        close_hover = hovered and self.kind == "close"
        if hovered or self.isDown():
            fill = ("#A82318" if self.isDown() else "#C42B1C") if close_hover else colors["selection" if self.isDown() else "hover"]
            painter.fillRect(self.rect(), QColor(fill))
        painter.setPen(QPen(QColor("#FFFFFF" if close_hover else colors["text"]), 1.2))
        painter.translate(self.width() / 2, self.height() / 2)
        if self.kind == "minimize":
            painter.drawLine(-5, 0, 5, 0)
        elif self.kind == "maximize":
            if self.host.isMaximized():
                painter.drawLine(-3, -5, 5, -5)
                painter.drawLine(5, -5, 5, 3)
                painter.drawRect(QRectF(-5, -3, 8, 8))
            else:
                painter.drawRect(QRectF(-4.5, -4.5, 9, 9))
        else:
            painter.drawLine(-4, -4, 4, 4)
            painter.drawLine(4, -4, -4, 4)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class WindowTitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(32)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)
        self.mark = QLabel()
        self.mark.setFixedSize(20, 20)
        title = QLabel(window.windowTitle())
        title.setObjectName("windowTitle")
        for label in (self.mark, title):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        window.windowTitleChanged.connect(title.setText)
        layout.addStretch()
        buttons = QWidget()
        controls = QHBoxLayout(buttons)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        self.minimize = CaptionButton("minimize", window, buttons)
        self.maximize = CaptionButton("maximize", window, buttons)
        self.close_button = CaptionButton("close", window, buttons)
        for button in (self.minimize, self.maximize, self.close_button):
            controls.addWidget(button)
        layout.addWidget(buttons)
        self.minimize.clicked.connect(window.showMinimized)
        self.maximize.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        window.installEventFilter(self)
        self.refresh()

    def refresh(self):
        for button, text in ((self.minimize, "最小化"), (self.maximize, "还原" if self.host.isMaximized() else "最大化"), (self.close_button, "关闭")):
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.update()
        colors = self.host.colors
        icon = outline_icon("waveform", colors["text"], colors["disabled"])
        self.mark.setPixmap(icon.pixmap(20, 20))
        self.host.setWindowIcon(icon)

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.refresh()
        return super().eventFilter(watched, event)

    def toggle_maximized(self):
        self.host.showNormal() if self.host.isMaximized() else self.host.showMaximized()

    def is_drag_area(self, position):
        point = self.mapFrom(self.host, position)
        if not self.rect().contains(point):
            return False
        child = self.childAt(point)
        while child is not None and child is not self:
            if isinstance(child, QAbstractButton):
                return False
            child = child.parentWidget()
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.host.windowHandle():
            self.host.windowHandle().startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        restore = menu.addAction("还原", self.host.showNormal)
        restore.setEnabled(self.host.isMaximized())
        menu.addAction("最小化", self.host.showMinimized)
        maximize = menu.addAction("最大化", self.host.showMaximized)
        maximize.setEnabled(not self.host.isMaximized())
        menu.addSeparator()
        menu.addAction("关闭", self.host.close)
        menu.exec(event.globalPos())


class FrameWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._win_frame = None

    def configure_frame(self):
        from PySide6.QtWidgets import QApplication
        if sys.platform != "win32" or QApplication.platformName() != "windows" or not self.windowHandle():
            return
        if self._win_frame is None:
            self._win_frame = WindowsFrame(self)
        self._win_frame.configure()

    def showEvent(self, event):
        super().showEvent(event)
        self.configure_frame()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.setProperty("frameMaximized", self.isMaximized() or self.isFullScreen())
            self.style().unpolish(self)
            self.style().polish(self)

    def nativeEvent(self, event_type, message):
        if self._win_frame is not None:
            result = self._win_frame.handle(int(message))
            if result is not None:
                return True, result
        return super().nativeEvent(event_type, message)


class WindowsFrame:
    """Use Windows hit testing for resize, move, taskbar bounds and Alt+F4."""
    def __init__(self, window):
        import ctypes as ct
        from ctypes import wintypes as wt
        self.ct, self.wt, self.window = ct, wt, window
        self.user = ct.WinDLL("user32", use_last_error=True)
        self.dwm = ct.WinDLL("dwmapi")
        class MonitorInfo(ct.Structure):
            _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD)]
        class MinMaxInfo(ct.Structure):
            _fields_ = [(key, wt.POINT) for key in ("reserved", "max_size", "max_position", "min_track", "max_track")]
        self.MonitorInfo, self.MinMaxInfo = MonitorInfo, MinMaxInfo
        signatures = {
            "GetWindowLongPtrW": ([wt.HWND, ct.c_int], ct.c_ssize_t),
            "SetWindowLongPtrW": ([wt.HWND, ct.c_int, ct.c_ssize_t], ct.c_ssize_t),
            "SetWindowPos": ([wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT], wt.BOOL),
            "MonitorFromWindow": ([wt.HWND, wt.DWORD], wt.HANDLE),
            "GetMonitorInfoW": ([wt.HANDLE, ct.POINTER(MonitorInfo)], wt.BOOL),
            "IsZoomed": ([wt.HWND], wt.BOOL),
            "GetWindowRect": ([wt.HWND, ct.POINTER(wt.RECT)], wt.BOOL),
            "ScreenToClient": ([wt.HWND, ct.POINTER(wt.POINT)], wt.BOOL),
            "GetDpiForWindow": ([wt.HWND], wt.UINT),
            "GetSystemMetricsForDpi": ([ct.c_int, wt.UINT], ct.c_int),
        }
        for name, (args, returns) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, returns
        self.dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD]
        self.dwm.DwmExtendFrameIntoClientArea.argtypes = [wt.HWND, ct.c_void_p]

    def configure(self):
        ct, user, window = self.ct, self.user, self.window
        handle = int(window.winId())
        previous = user.GetWindowLongPtrW(handle, -16)
        required = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        if previous & required != required:
            user.SetWindowLongPtrW(handle, -16, previous | required)
            user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x37)
        dark = ct.c_int(window.theme_name == "dark")
        # Preserve the requested square corners rather than the reference's rounding.
        square = ct.c_int(1)
        color = QColor(window.colors["border"])
        border = ct.c_uint(color.red() | color.green() << 8 | color.blue() << 16)
        for attribute, value in ((20, dark), (33, square), (34, border)):
            self.dwm.DwmSetWindowAttribute(handle, attribute, ct.byref(value), ct.sizeof(value))
        shadow = (ct.c_int * 4)(1, 1, 1, 1)
        self.dwm.DwmExtendFrameIntoClientArea(handle, ct.byref(shadow))

    def monitor(self, handle):
        info = self.MonitorInfo()
        info.size = self.ct.sizeof(info)
        if self.user.GetMonitorInfoW(self.user.MonitorFromWindow(handle, 2), self.ct.byref(info)):
            return info

    def handle(self, address):
        ct, wt, user, window = self.ct, self.wt, self.user, self.window
        msg = wt.MSG.from_address(address)
        if msg.message == 0x83 and msg.lParam:  # WM_NCCALCSIZE
            if user.IsZoomed(msg.hWnd) and not window.isFullScreen():
                info = self.monitor(msg.hWnd)
                if info:
                    rect = wt.RECT.from_address(msg.lParam)
                    rect.left, rect.top, rect.right, rect.bottom = info.work.left, info.work.top, info.work.right, info.work.bottom
            return 0
        if msg.message == 0x24 and msg.lParam:  # WM_GETMINMAXINFO
            info = self.monitor(msg.hWnd)
            if info:
                limits = self.MinMaxInfo.from_address(msg.lParam)
                limits.max_position = wt.POINT(info.work.left - info.monitor.left, info.work.top - info.monitor.top)
                limits.max_size = wt.POINT(info.work.right - info.work.left, info.work.bottom - info.work.top)
                scale = window.devicePixelRatioF()
                limits.min_track = wt.POINT(round(window.minimumWidth() * scale), round(window.minimumHeight() * scale))
                return 0
        if msg.message == 0x84:  # WM_NCHITTEST, signed coordinates support negative monitors.
            point = wt.POINT(ct.c_short(msg.lParam & 0xFFFF).value, ct.c_short((msg.lParam >> 16) & 0xFFFF).value)
            bounds = wt.RECT()
            if not user.GetWindowRect(msg.hWnd, ct.byref(bounds)):
                return None
            if not user.IsZoomed(msg.hWnd) and not window.isFullScreen():
                dpi = user.GetDpiForWindow(msg.hWnd)
                edge = user.GetSystemMetricsForDpi(32, dpi) + user.GetSystemMetricsForDpi(92, dpi)
                left, right = point.x < bounds.left + edge, point.x >= bounds.right - edge
                top, bottom = point.y < bounds.top + edge, point.y >= bounds.bottom - edge
                hit = (13 if left else 14 if right else 12) if top else ((16 if left else 17 if right else 15) if bottom else (10 if left else 11 if right else 1))
                if hit != 1:
                    return hit
            user.ScreenToClient(msg.hWnd, ct.byref(point))
            scale = window.devicePixelRatioF()
            if hasattr(window, "title_bar") and window.title_bar.is_drag_area(QPoint(round(point.x / scale), round(point.y / scale))):
                return 2
            return 1
        return None
