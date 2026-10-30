"""Controlled Qt layout/state QA. No microphone, playback, or real enrollment.

Run separately per QT_SCALE_FACTOR so font and scaling changes are applied by Qt.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtWidgets import QApplication, QPushButton
    from PySide6.QtCore import Qt, QPointF, QRect
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    import numpy as np
    from dsp_lab.ui import MainWindow
    from dsp_lab.audio import History

    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="1")
    parser.add_argument("--output", default="validation/long-session")
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    # The Windows offscreen platform does not discover installed fonts by itself.
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "consola.ttf", "consolab.ttf"):
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    window = MainWindow(ROOT, test_mode=True)
    window.show()
    deadline = time.monotonic() + 15
    while not window.prepared and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window.prepared, "Real model preparation did not finish"
    window.timer.stop()
    assertions = []
    screenshots = []

    def check(name, value):
        assertions.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)

    def snapshot(name):
        app.processEvents()
        path = output / f"{name}-scale-{args.scale}.png"
        window.grab().save(str(path))
        screenshots.append(path.name)

    def visible_in_window(widget):
        point = widget.mapTo(window, widget.rect().center())
        return widget.isVisible() and window.rect().contains(point)

    try:
        check("idle_start_matches_action", window.start_button.isEnabled() == window.start_action.isEnabled())
        check("empty_export_disabled", not window.export_button.isEnabled() and not window.export_action.isEnabled())
        check("empty_member_state", "暂无" in window.profile_hint.text() and not window.speaker_table.isVisible())
        check("empty_plots_have_next_step", all(label.isVisibleTo(label.parentWidget()) and "启动处理" in label.text() for label in window.empty_plot_labels))
        for theme in ("light", "dark"):
            window.theme_name = theme
            window._apply_theme()
            for width, height in ((900, 580), (1024, 768), (1440, 900)):
                window.resize(width, height)
                app.processEvents()
                window.control_tabs.setCurrentIndex(0)
                window.chart_tabs.setCurrentIndex(0)
                app.processEvents()
                check(f"{theme}_{width}_actual_size", window.width() == width and window.height() == height)
                check(f"{theme}_{width}_primary_visible", visible_in_window(window.start_button))
                check(f"{theme}_{width}_plot_and_result_visible", visible_in_window(window.wave) and visible_in_window(window.speaker_label))
                check(f"{theme}_{width}_monitor_visible", visible_in_window(window.monitor))
                buttons = (window.start_button, window.export_button, window.theme_button, window.help_button)
                rectangles = [button.geometry() for button in buttons]
                check(f"{theme}_{width}_header_buttons_do_not_overlap", all(not a.intersects(b) for a, b in zip(rectangles, rectangles[1:])))
                check(f"{theme}_{width}_button_text_fits", all(
                    button.fontMetrics().horizontalAdvance(button.text()) + 16 + 28 <= button.width()
                    for button in buttons[:2]))
                check(f"{theme}_{width}_toolbar_reflows", window.toolbar.stacked == (width < 1120))
                check(f"{theme}_{width}_sources_remain_readable", all(combo.width() >= 280 for combo in (window.input_combo, window.output_combo)))
                check(f"{theme}_{width}_toolbar_widgets_fully_visible", all(
                    window.rect().contains(QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size()))
                    for widget in (*buttons, window.input_combo, window.output_combo)))
                snapshot(f"{theme}-{width}x{height}")
            check(f"{theme}_icons_present", all(not button.icon().isNull() for button in buttons))
            check(f"{theme}_icon_buttons_labeled", bool(window.theme_button.accessibleName()) and bool(window.help_button.accessibleName()))
        window.resize(1024, 768)
        app.processEvents()
        combo = window.input_combo
        original_index, original_id = combo.currentIndex(), combo.currentData()
        long_name = "受控设备 · 课程采集麦克风阵列 / USB Audio Interface with Extended Device Name [MME]"
        combo.addItem(long_name, original_id)
        controlled_index = combo.count() - 1
        combo.setItemData(controlled_index, long_name, Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentIndex(controlled_index)
        app.processEvents()
        check("long_device_keeps_identity", combo.currentData() == original_id and combo.currentText() == long_name)
        check("long_device_keeps_host_suffix", "…" in combo.display_text() and combo.display_text().endswith("[MME]"))
        check("long_device_tooltip_is_complete", combo.toolTip() == long_name)
        combo.showPopup()
        app.processEvents()
        screen_width = combo.screen().availableGeometry().width()
        if screen_width > combo.width() + 40:
            check("device_popup_expands_for_full_source", combo.view().width() > combo.width() and combo.view().width() <= screen_width)
        else:
            # Offscreen's 800px virtual display becomes 400 logical px at 2x,
            # smaller than the 900px application contract; Qt keeps popup >= field.
            check("device_popup_at_virtual_screen_limit", combo.view().minimumWidth() <= screen_width and combo.view().width() <= combo.width() + 16)
        popup_path = output / f"dark-device-popup-controlled-scale-{args.scale}.png"
        combo.view().window().grab().save(str(popup_path))
        QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
        app.processEvents()
        check("device_popup_escape_preserves_selection", not combo.view().isVisible() and combo.currentData() == original_id)
        combo.setCurrentIndex(original_index)
        combo.removeItem(controlled_index)
        for width in (1119, 1120, 1121, 900, 1440, 1024):
            window.resize(width, 768)
            app.processEvents()
            check(f"toolbar_boundary_{width}", window.toolbar.stacked == (window.toolbar.width() < 1120))
        check("reflow_preserves_source_id", combo.currentData() == original_id)
        window.theme_name = "light"
        window._apply_theme()
        window.echo.setChecked(True)
        window.echo_delay.setValue(235)
        window.echo_gain.setValue(0.6)
        window.member_name.setText("课程测试草稿")
        window.control_tabs.setCurrentIndex(1)
        window.chart_tabs.setCurrentIndex(1)
        window.theme_button.setFocus()
        QTest.keyClick(window.theme_button, Qt.Key.Key_Space)
        check("theme_keyboard_activation", window.theme_name == "dark")
        check("theme_retains_controls", window.echo_delay.value() == 235 and window.member_name.text() == "课程测试草稿")
        check("theme_retains_tabs", window.control_tabs.currentIndex() == 1 and window.chart_tabs.currentIndex() == 1)
        frequency_range = window.spectrum.viewRange()[0]
        check("spectrum_axis_retains_hz_range", abs(frequency_range[0]) < 0.01 and abs(frequency_range[1] - 8000) < 0.01)
        check("effects_bound_to_engine", window.engine.settings.echo_ms == 235 and abs(window.engine.settings.echo_gain - 0.6) < 0.01)
        snapshot("dark-reference-spectrum")
        window.members_button.click()
        check("manage_members_opens_registration", window.control_tabs.currentIndex() == 2)
        window.service.profiles = {"受控样例甲": {"count": 2}, "受控样例乙": {"count": 3}}
        window._refresh_profiles()
        window.speaker_table.selectRow(1)
        window._refresh_profiles([{"name": "受控样例甲", "similarity": 0.2}, {"name": "受控样例乙", "similarity": 0.8}])
        selected = window.speaker_table.selectionModel().selectedRows()
        check("profile_refresh_retains_identity", len(selected) == 1 and window.speaker_table.item(selected[0].row(), 0).text() == "受控样例乙")
        check("profile_score_updates", window.speaker_table.item(1, 2).text() == "0.800")
        check("numeric_columns_aligned", bool(window.speaker_table.item(1, 2).textAlignment() & Qt.AlignmentFlag.AlignRight))
        snapshot("dark-members-controlled")
        window._show_diagnostics()
        app.processEvents()
        check("diagnostics_opens", window.diagnostics.isVisible())
        check("unavailable_data_not_zero", window.diagnostic_values["每帧处理"].text() == "—")
        dialog_path = output / f"dark-diagnostics-scale-{args.scale}.png"
        window.diagnostics.grab().save(str(dialog_path))
        QTest.keyClick(window.diagnostics, Qt.Key.Key_Escape)
        app.processEvents()
        check("escape_closes_only_diagnostics", not window.diagnostics.isVisible() and window.isVisible())
        # Controlled transport hook exercises real buttons/actions without audio hardware.
        engine = window.engine
        engine._ui_fake_running = False
        original_running = type(engine).running
        original_stop = engine.stop
        type(engine).running = property(lambda e: e._ui_fake_running)
        def fake_start(*_):
            engine._ui_fake_running = True
            engine.stream = SimpleNamespace(active=True, latency=(0.016, 0.096))
            engine.history = History()
            engine.stats = {}
        def fake_stop():
            engine._ui_fake_running = False
            engine.stream = None
        engine.start = fake_start
        engine.stop = fake_stop
        try:
            window.start_button.click()
            window._tick()
            check("button_starts_real_ui_state", engine.running and window.start_action.text() == "停止处理")
            check("running_locks_devices", not window.input_combo.isEnabled())
            check("running_stop_is_distinct", window.start_button.property("wbRunning") is True)
            check("missing_processing_metric_is_unknown", "—" in window.metrics.text())
            current_stream = engine.stream
            for width in (1440, 900, 1024):
                window.resize(width, 768)
                app.processEvents()
            check("reflow_does_not_restart_running_audio", engine.stream is current_stream and engine.running)
            check("reflow_preserves_drafts_and_settings", window.member_name.text() == "课程测试草稿" and window.engine.settings.echo_ms == 235)
            window.noise_button.click()
            check("noise_command_bound", engine.settings.learn_token > 0)
            data = np.sin(np.arange(256) * 0.06).astype(np.float32) * 0.2
            engine.history.append(data, data * 0.8, data * 0, data)
            engine.stats = {"processing_ms": 0.8, "noise_profile": True, "xruns": 0, "deadline_misses": 0}
            window._tick()
            deadline = time.monotonic() + 3
            while window.clean_curve.getData()[0] is None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
                window._tick()
            check("captured_data_enables_export", window.export_action.isEnabled() and window.export_button.isEnabled())
            check("real_data_hides_empty_labels", all(not label.isVisible() for label in window.empty_plot_labels))
            position = window.wave.plotItem.vb.mapViewToScene(QPointF(-0.005, 0))
            window._show_plot_value(window.wave, position)
            check("wave_tooltip_uses_data", "时间" in window.wave.toolTip() and "处理后" in window.wave.toolTip())
            window.control_tabs.setCurrentIndex(0)
            window.chart_tabs.setCurrentIndex(0)
            window.theme_name = "light"
            window._apply_theme()
            snapshot("light-running-controlled")
            window.start_action.trigger()
            check("shared_action_stops", not engine.running and window.input_combo.isEnabled())
            check("stopped_audio_retains_export", window.export_action.isEnabled())
            check("stop_returns_start_appearance", window.start_button.property("wbRunning") is False)
            window.start_action.trigger()
            window._tick()
            check("new_session_removes_previous_curves", window.raw_curve.getData()[0] is None and window.clean_spectrum.getData()[0] is None)
            check("new_session_restores_honest_empty_state", all(not label.isHidden() for label in window.empty_plot_labels) and not window.export_action.isEnabled())
            check("new_session_clears_old_readout", not window.wave.toolTip() and not window.wave.accessibleDescription())
            window.start_action.trigger()
        finally:
            engine.stop = original_stop
            type(engine).running = original_running
        window.last_error = "受控状态：输入设备未连接"
        window.status_label.setText(window.last_error)
        window._update_actions()
        check("error_uses_text_and_color", "需要处理" in window.state_badge.text() and window.state_badge.property("wbTone") == "error")
        snapshot("light-error-controlled")
        result = {
            "passed": all(item["passed"] for item in assertions), "assertions": assertions,
            "screenshots": screenshots, "requested_scale": args.scale,
            "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR"),
            "window_device_pixel_ratio": window.devicePixelRatioF(),
            "screens": [{"name": s.name(), "logical_geometry": s.geometry().getRect(),
                         "dpr": s.devicePixelRatio(), "logical_dpi": s.logicalDotsPerInch()} for s in app.screens()],
            "controlled_states": ["profiles", "audio_transport", "running_waveform"],
            "real_microphone_opened": False,
        }
        (output / f"qa-scale-{args.scale}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": result["passed"], "assertions": len(assertions), "screenshots": len(screenshots), "dpr": result["window_device_pixel_ratio"]}))
    finally:
        window.service.profiles = {}
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
