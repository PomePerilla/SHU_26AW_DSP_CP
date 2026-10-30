"""Exercise only this application's test window; no microphone or external UI.

Checks the newly adapted Windows chrome, including native hit testing and
work-area bounds. Real cross-monitor movement remains a manual acceptance item.
"""
from pathlib import Path
import ctypes as ct
from ctypes import wintypes as wt
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from dsp_lab.ui import MainWindow

    app = QApplication([])
    app.setStyle("Fusion")
    app.setApplicationName("DSP Studio")
    if app.platformName() != "windows":
        raise RuntimeError("This check needs the native Windows Qt platform")
    folder = ROOT / "validation/long-session"
    folder.mkdir(parents=True, exist_ok=True)
    window = MainWindow(ROOT, test_mode=True)
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    def settle():
        QTest.qWait(120)
        app.processEvents()

    try:
        window.resize(1024, 768)
        window.show()
        deadline = time.monotonic() + 15
        while not window.prepared and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        check("model_prepared", window.prepared)
        window.timer.stop()
        settle()
        frame = window._win_frame
        check("native_frame_initialized", frame is not None)
        user = frame.user
        user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user.SendMessageW.restype = ct.c_ssize_t
        user.ClientToScreen.argtypes = [wt.HWND, ct.POINTER(wt.POINT)]
        user.GetClientRect.argtypes = [wt.HWND, ct.POINTER(wt.RECT)]
        handle = int(window.winId())

        def hit(x, y):
            point = wt.POINT(round(x * window.devicePixelRatioF()), round(y * window.devicePixelRatioF()))
            user.ClientToScreen(handle, ct.byref(point))
            return user.SendMessageW(handle, 0x84, 0, (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16))

        def hit_widget(widget):
            point = widget.mapTo(window, widget.rect().center())
            return hit(point.x(), point.y())

        check("normal_title_drag_hit", hit(200, 16) == 2)
        check("close_button_not_drag_area", hit_widget(window.title_bar.close_button) == 1)
        check("device_input_not_drag_area", hit_widget(window.input_combo) == 1)
        check("stacked_actions_not_drag_area", window.toolbar.stacked and hit_widget(window.start_button) == 1)
        device_id = window.input_combo.currentData()
        window.input_combo.showPopup()
        settle()
        view = window.input_combo.view()
        check("native_device_popup_has_room", view.width() >= window.input_combo.width() and view.width() <= window.screen().availableGeometry().width())
        QTest.keyClick(view, Qt.Key.Key_Escape)
        settle()
        check("native_popup_escape_keeps_source", not view.isVisible() and window.input_combo.currentData() == device_id)
        bounds = wt.RECT()
        user.GetWindowRect(handle, ct.byref(bounds))
        edge_point = (bounds.left + 1, bounds.top + 1)
        packed = (edge_point[0] & 0xFFFF) | ((edge_point[1] & 0xFFFF) << 16)
        check("top_left_resize_hit", user.SendMessageW(handle, 0x84, 0, packed) == 13)
        window.grab().save(str(folder / "native-dark-1024.png"))
        QTest.mouseClick(window.title_bar.maximize, Qt.MouseButton.LeftButton)
        settle()
        check("maximize_button", window.isMaximized())
        user.GetWindowRect(handle, ct.byref(bounds))
        info = frame.monitor(handle)
        outer = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        # A maximized Windows thick frame extends outside the visible work area.
        # The client rectangle, rather than that hidden border, must fit exactly.
        client = wt.RECT()
        origin = wt.POINT()
        user.GetClientRect(handle, ct.byref(client))
        user.ClientToScreen(handle, ct.byref(origin))
        actual = (origin.x, origin.y, origin.x + client.right, origin.y + client.bottom)
        expected = (info.work.left, info.work.top, info.work.right, info.work.bottom)
        check("maximize_respects_taskbar_work_area", actual == expected)
        check("maximize_label_updates", window.title_bar.maximize.accessibleName() == "还原")
        QTest.mouseClick(window.title_bar.maximize, Qt.MouseButton.LeftButton)
        settle()
        check("restore_button", not window.isMaximized() and window.size().width() == 1024)
        QTest.mouseDClick(window.title_bar, Qt.MouseButton.LeftButton, pos=QPoint(200, 16))
        settle()
        check("title_double_click_maximizes", window.isMaximized())
        window.showNormal()
        settle()
        QTest.mouseClick(window.title_bar.minimize, Qt.MouseButton.LeftButton)
        settle()
        check("minimize_button", window.isMinimized())
        window.showNormal()
        settle()
        window.theme_name = "light"
        window._apply_theme()
        settle()
        check("theme_preserves_window_controls", window.title_bar.maximize.accessibleName() == "最大化")
        window.grab().save(str(folder / "native-light-1024.png"))
        QTest.mouseClick(window.title_bar.close_button, Qt.MouseButton.LeftButton)
        settle()
        check("close_button_stops_ui_tasks", not window.isVisible() and not window.timer.isActive() and window.pool._shutdown)
        result = {"passed": all(c["passed"] for c in checks), "checks": checks,
                  "platform": app.platformName(), "dpr": window.devicePixelRatioF(),
                  "maximized_outer_rect": outer, "maximized_client_rect": actual, "work_area": expected,
                  "microphone_opened": False,
                  "scope": "Qt own-process caption actions and Windows native frame hit tests; no external app input"}
        (folder / "native-frame.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if window.isVisible():
            window.close()


if __name__ == "__main__":
    main()
