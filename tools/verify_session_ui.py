"""Session controls/axis input QA with explicitly synthetic temporary recordings."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtWidgets import QApplication, QTableWidget
    from PySide6.QtCore import Qt, QPoint, QPointF, QTimer, QRect
    from PySide6.QtGui import QFontDatabase, QWheelEvent
    from PySide6.QtTest import QTest
    from dsp_lab.ui import MainWindow
    from dsp_lab.sessions import SessionWriter, list_sessions
    from dsp_lab.core import RATE

    app = QApplication([])
    app.setStyle("Fusion")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    output = ROOT / "validation/long-session"
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "native")
    window = MainWindow(ROOT, test_mode=True)
    window.resize(1024, 768)
    window.show()
    checks = []
    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)
    def pump():
        app.processEvents()
        window._tick()
    def settle_plot():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pump()
            x, _ = window.raw_curve.getData()
            if x is not None and len(x):
                return
            time.sleep(0.01)
        raise AssertionError("plot read timed out")
    try:
        deadline = time.monotonic() + 15
        while not window.prepared and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        window.timer.stop()
        check("no_automatic_recording_in_test", not window.preferences.auto_record)
        check("settings_is_fourth_tab", window.control_tabs.tabText(3) == "设置")
        check("default_span_ten_seconds", window.preferences.span == 10)
        check("vertical_axes_locked_by_default", not window.wave.plotItem.vb.state["mouseEnabled"][1] and not window.spectrum.plotItem.vb.state["mouseEnabled"][1])
        check("native_plot_menus_disabled", not window.wave.plotItem.menuEnabled() and not window.wave.plotItem.vb.menuEnabled())
        check("auto_zoom_buttons_hidden", not window.wave.plotItem.autoBtn.isVisible())
        with tempfile.TemporaryDirectory(prefix="dsp-session-ui-") as directory:
            folder = Path(directory) / "synthetic"
            writer = SessionWriter(folder)
            for second in range(65):
                time_axis = (np.arange(RATE) + second * RATE) / RATE
                envelope = (0.25 + 0.75 * np.sin(2 * np.pi * 0.3 * time_axis) ** 2) * ((time_axis % 7) < 5)
                raw = (0.3 * envelope * np.sin(2 * np.pi * 300 * time_axis)).astype(np.float32)
                data = np.column_stack((raw, raw * 0.5, raw * 0, raw * 0.8))
                writer.append(data)
                window.engine.history.append(*data.T)
            writer.close()
            window.preferences.recording_directory = directory
            def close_dialog():
                try:
                    window.session_dialog.grab().save(str(output / f"session-list-controlled-scale-{scale}.png"))
                    check("session_dialog_lists_complete_recording", window.session_dialog.findChild(QTableWidget).rowCount() == 1)
                finally:
                    window.session_dialog.reject()
            QTimer.singleShot(100, close_dialog)
            window.session_button.click()
            window.engine.recorder.status = {"status": "complete", "folder": str(folder), "frames": RATE * 65, "error": ""}
            settle_plot()
            check("live_time_axis_is_absolute", np.allclose(window.wave.viewRange()[0], [55, 65], atol=0.01))
            window._show_entire()
            settle_plot()
            check("entire_session_includes_older_than_ring", np.allclose(window.wave.viewRange()[0], [0, 65], atol=0.01))
            window._set_wave_range(10, 30)
            window.fit_whole = False
            app.processEvents()
            center = window.wave.mapFromScene(window.wave.plotItem.vb.sceneBoundingRect().center())
            wheel = QWheelEvent(QPointF(center), QPointF(window.wave.mapToGlobal(center)), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            before = window.wave.viewRange()
            app.sendEvent(window.wave.viewport(), wheel)
            app.processEvents()
            after = window.wave.viewRange()
            check("wheel_zoom_changes_only_x", not np.allclose(before[0], after[0]) and np.allclose(after[1], [-1, 1]))
            check("manual_input_disables_follow", not window.follow.isChecked())
            QTest.mousePress(window.wave.viewport(), Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(window.wave.viewport(), center + QPoint(40, 50), delay=20)
            QTest.mouseRelease(window.wave.viewport(), Qt.MouseButton.LeftButton, pos=center + QPoint(40, 50))
            check("drag_cannot_move_vertical_axis", np.allclose(window.wave.viewRange()[1], [-1, 1]))
            window.wave_lock.setChecked(False)
            window.wave.setYRange(-0.2, 0.2, padding=0)
            check("settings_can_unlock_y", window.wave.plotItem.vb.state["mouseEnabled"][1] and np.allclose(window.wave.viewRange()[1], [-0.2, 0.2]))
            window.reset_view_button.click()
            check("reset_restores_standard_y_and_follow", np.allclose(window.wave.viewRange()[1], [-1, 1]) and window.follow.isChecked())
            window.wave_lock.setChecked(True)
            check("plot_menu_stays_disabled_after_interaction", not window.wave.plotItem.menuEnabled() and not window.wave.plotItem.autoBtn.isVisible())
            window.spectrum_lock.setChecked(False)
            check("spectrum_y_unlocks_independently", window.spectrum.plotItem.vb.state["mouseEnabled"][1] and not window.wave.plotItem.vb.state["mouseEnabled"][1])
            window.spectrum_lock.setChecked(True)
            window.span_combo.setCurrentIndex(3)  # 30 s
            pump()
            check("span_setting_applies_immediately", np.allclose(window.wave.viewRange()[0], [35, 65], atol=0.01))
            window.control_tabs.setCurrentIndex(3)
            window.grab().save(str(output / f"dark-settings-controlled-scale-{scale}.png"))
            window._load_session(folder)
            settle_plot()
            check("history_has_explicit_source", window.historical and window.source_caption.full_text == folder.name)
            check("history_disables_live_actions", not window.start_action.isEnabled() and not window.control_tabs.isTabEnabled(0) and not window.monitor.isEnabled())
            check("history_keeps_playback_and_output", window.playback_bar.isVisible() and window.output_combo.isEnabled())
            check("history_hides_live_recognition", window.speaker_panel.isHidden())
            for theme in ("dark", "light"):
                window.theme_name = theme
                window._apply_theme()
                for width, height in ((900, 580), (1024, 768), (1440, 900)):
                    window.resize(width, height)
                    app.processEvents()
                    check(f"history_{theme}_{width}_size", window.size().width() == width and window.size().height() == height)
                    check(f"history_{theme}_{width}_controls_fit", all(
                        window.rect().contains(QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size()))
                        for widget in (window.play_button, window.play_slider, window.play_track, window.return_live, window.play_position, window.play_duration)))
                    if width == 900:
                        window.grab().save(str(output / f"{theme}-history-900-controlled-scale-{scale}.png"))
            window.resize(1024, 768)
            app.processEvents()
            window.play_slider.setValue(500000)
            window._seek_playback()
            pump()
            check("seek_moves_cursor_to_session_time", window.player.position == RATE * 65 // 2 and abs(window.cursor.value() - 32.5) < 0.01)
            window.play_track.setCurrentIndex(1)
            check("track_switch_keeps_position", window.player.track == 0 and window.player.position == RATE * 65 // 2)
            window.theme_name = "light"
            window._apply_theme()
            app.processEvents()
            window.grab().save(str(output / f"light-history-controlled-scale-{scale}.png"))
            window._return_to_live()
            pump()
            check("return_live_releases_history_and_player", not window.historical and window.player.process is None and not window.playback_bar.isVisible())
            check("return_live_clears_old_curves", window.raw_curve.getData()[0] is None and window.wave.toolTip() == "")
            # An uncleanly terminated writer is recovered as interrupted.
            stale_folder = Path(directory) / "interrupted"
            stale = SessionWriter(stale_folder)
            stale.append(np.zeros((37, 4), np.float32))
            stale.close()
            metadata = json.loads((stale_folder / "session.json").read_text(encoding="utf-8"))
            metadata.update(status="recording", writer_pid=0)
            (stale_folder / "session.json").write_text(json.dumps(metadata), encoding="utf-8")
            check("unfinished_session_recovers_as_incomplete", list_sessions(directory)[0][1]["status"] in ("complete", "interrupted") and json.loads((stale_folder / "session.json").read_text())["status"] == "interrupted")
        result = {"passed": all(c["passed"] for c in checks), "checks": checks, "microphone_opened": False,
                  "fixtures": "synthetic 65-second session in temporary directory", "dpr": window.devicePixelRatioF()}
        (output / f"session-ui-{window.devicePixelRatioF():g}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": result["passed"], "checks": len(checks), "dpr": result["dpr"]}))
    finally:
        window.close()


if __name__ == "__main__":
    main()
