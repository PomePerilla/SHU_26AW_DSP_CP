"""Session-aware controls and plotting, kept separate from DSP parameter UI."""
from pathlib import Path
import time
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                              QCheckBox, QComboBox, QLineEdit, QFileDialog, QSlider, QDialog,
                              QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox)
from .core import RATE
from .audio import History
from .preferences import Preferences, SPANS
from .sessions import RecordingOptions, SessionReader, list_sessions
from .playback import PlaybackClient
from .waveforms import WaveformBrowser


def clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


class StudioFeatures:
    def _init_studio(self, test_mode):
        self.test_mode = test_mode
        self.preferences = Preferences(recording_directory=str(self.root / "data/recordings"), auto_record=False) if test_mode else Preferences.load(self.root)
        self.player = PlaybackClient()
        self.browser = WaveformBrowser()
        self.history_reader = None
        self.view_generation = 0
        self.programmatic_view = False
        self.fit_whole = False
        self.last_plot_request = None
        self.last_play_error = ""
        self.record_error = ""
        self.plot_error = ""
        self.plot_retry_at = 0
        self.session_dialog = None

    @property
    def historical(self):
        return self.history_reader is not None

    def _build_display_settings(self, layout):
        for title in ("图表显示",):
            label = QLabel(title)
            label.setProperty("wbRole", "section")
            label.setFixedHeight(28)
            layout.addWidget(label)
        row = QHBoxLayout()
        self.span_combo = QComboBox()
        self.span_combo.setAccessibleName("默认显示跨度")
        for span in SPANS:
            self.span_combo.addItem(f"{span} s", span)
        self.span_combo.setCurrentIndex(SPANS.index(self.preferences.span))
        self.span_combo.currentIndexChanged.connect(self._preferences_changed)
        row.addWidget(QLabel("默认跨度"), 1)
        row.addWidget(self.span_combo)
        layout.addLayout(row)
        self.wave_lock = QCheckBox("锁定波形纵轴")
        self.spectrum_lock = QCheckBox("锁定频谱纵轴")
        for control, checked in ((self.wave_lock, self.preferences.wave_locked), (self.spectrum_lock, self.preferences.spectrum_locked)):
            control.setChecked(checked)
            control.toggled.connect(self._preferences_changed)
            layout.addWidget(control)
        hint = QLabel("锁定时只允许横向拖动和缩放。")
        hint.setProperty("wbRole", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        label = QLabel("会话录音")
        label.setProperty("wbRole", "section")
        label.setFixedHeight(28)
        layout.addWidget(label)
        self.auto_record = QCheckBox("启动处理时保存完整会话")
        self.auto_record.setChecked(self.preferences.auto_record)
        self.auto_record.toggled.connect(self._preferences_changed)
        layout.addWidget(self.auto_record)
        self.record_directory = QLineEdit(self.preferences.recording_directory)
        self.record_directory.setAccessibleName("录音目录")
        self.record_directory.setReadOnly(True)
        self.record_directory.setCursorPosition(0)
        self.record_directory.setToolTip(self.preferences.recording_directory)
        layout.addWidget(self.record_directory)
        folder = QPushButton("选择录音目录…")
        folder.setProperty("wbRole", "secondary")
        folder.clicked.connect(self._choose_record_directory)
        layout.addWidget(folder)
        hint = QLabel("录音设置从下一次采集生效。四路音频约 0.92 GB/小时。")
        hint.setProperty("wbRole", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.recording_label = QLabel("未开始录音")
        self.recording_label.setWordWrap(True)
        self.recording_label.setProperty("wbRole", "muted")
        layout.addWidget(self.recording_label)
        layout.addStretch()

    def _build_plot_tools(self):
        frame = QFrame()
        frame.setProperty("wbRole", "bar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(8)
        self.follow = QCheckBox("跟随实时")
        self.follow.setChecked(True)
        self.follow.toggled.connect(self._follow_changed)
        row.addWidget(self.follow)
        self.entire_button = QPushButton("显示整段")
        self.entire_button.clicked.connect(self._show_entire)
        row.addWidget(self.entire_button)
        self.reset_view_button = QPushButton("复位视图")
        self.reset_view_button.clicked.connect(self._reset_view)
        row.addWidget(self.reset_view_button)
        row.addStretch()
        self.return_live = QPushButton("返回实时")
        self.return_live.clicked.connect(self._return_to_live)
        self.return_live.hide()
        row.addWidget(self.return_live)
        return frame

    def _build_playback_bar(self):
        self.playback_bar = QFrame()
        row = QHBoxLayout(self.playback_bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        self.play_button = QPushButton("播放")
        self.play_button.setMinimumWidth(58)
        self.play_button.clicked.connect(self._play_pause)
        row.addWidget(self.play_button)
        self.play_position = QLabel("00:00:00")
        self.play_position.setProperty("wbRole", "data")
        row.addWidget(self.play_position)
        self.play_slider = QSlider(Qt.Orientation.Horizontal)
        self.play_slider.setRange(0, 1000000)
        self.play_slider.setAccessibleName("历史播放进度")
        self.play_slider.sliderPressed.connect(self._begin_playback_seek)
        self.play_slider.sliderReleased.connect(self._seek_playback)
        row.addWidget(self.play_slider, 1)
        self.play_duration = QLabel("00:00:00")
        self.play_duration.setProperty("wbRole", "data")
        row.addWidget(self.play_duration)
        self.play_track = QComboBox()
        self.play_track.setAccessibleName("试听音轨")
        self.play_track.addItem("处理后", 1)
        self.play_track.addItem("原始", 0)
        self.play_track.currentIndexChanged.connect(self._change_playback_track)
        row.addWidget(self.play_track)
        self.playback_bar.hide()
        return self.playback_bar

    def _setup_graphs(self):
        for plot in (self.wave, self.spectrum):
            plot.plotItem.hideButtons()
            plot.setMenuEnabled(False)
            plot.plotItem.vb.setMouseMode(pg.ViewBox.PanMode)
            plot.plotItem.vb.sigRangeChangedManually.connect(self._manual_plot_range)
            plot.plotItem.vb.sigXRangeChanged.connect(self._plot_range_changed)
        self.cursor = pg.InfiniteLine(pos=0, angle=90, movable=False)
        self.cursor.hide()
        self.wave.addItem(self.cursor, ignoreBounds=True)
        self._apply_axis_policy()
        self._reset_view()

    def _apply_axis_policy(self):
        if not hasattr(self, "wave"):
            return
        for plot, locked, low, high in ((self.wave, self.preferences.wave_locked, -1, 1),
                                        (self.spectrum, self.preferences.spectrum_locked, -100, 0)):
            plot.setMouseEnabled(x=True, y=not locked)
            plot.setLimits(yMin=low if locked else None, yMax=high if locked else None,
                           minYRange=high - low if locked else 1e-6,
                           maxYRange=high - low if locked else None)
            if locked:
                plot.setYRange(low, high, padding=0)
        self.spectrum.setLimits(xMin=0, xMax=RATE / 2, minXRange=10, maxXRange=RATE / 2)

    def _preferences_changed(self, *_):
        if not hasattr(self, "auto_record"):
            return
        previous_span = self.preferences.span
        self.preferences.span = self.span_combo.currentData()
        self.preferences.wave_locked = self.wave_lock.isChecked()
        self.preferences.spectrum_locked = self.spectrum_lock.isChecked()
        self.preferences.auto_record = self.auto_record.isChecked()
        if not self.test_mode:
            try:
                self.preferences.save(self.root)
            except OSError as exc:
                self.status_label.setText(f"设置保存失败：{exc}")
        self._apply_axis_policy()
        if previous_span != self.preferences.span:
            self._reset_view()

    def _choose_record_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "选择录音目录", self.preferences.recording_directory)
        if folder:
            self.preferences.recording_directory = folder
            self.record_directory.setText(folder)
            self.record_directory.setCursorPosition(0)
            self.record_directory.setToolTip(folder)
            self._preferences_changed()

    def _duration_frames(self):
        return self.history_reader.frames if self.historical else self.engine.history.total

    def _follow_changed(self, checked):
        if checked:
            self.fit_whole = False
        self.last_plot_request = None

    def _manual_plot_range(self, *_):
        if not self.programmatic_view:
            self.follow.setChecked(False)
            self.fit_whole = False
            self.view_generation += 1
            self.last_plot_request = None

    def _plot_range_changed(self, *_):
        if not self.programmatic_view:
            self.view_generation += 1
            self.last_plot_request = None

    def _set_wave_range(self, start, stop):
        self.programmatic_view = True
        try:
            self.wave.setXRange(start, max(start + 0.1, stop), padding=0)
        finally:
            self.programmatic_view = False

    def _reset_view(self):
        self.fit_whole = False
        self.follow.setChecked(True)
        self._apply_axis_policy()
        self.wave.setYRange(-1, 1, padding=0)
        self.spectrum.setYRange(-100, 0, padding=0)
        self.spectrum.setXRange(0, RATE / 2, padding=0)
        frames = self._duration_frames()
        duration = max(0.1, frames / RATE) if frames else self.preferences.span
        end = self.player.position / RATE if self.historical else frames / RATE
        self._set_wave_range(max(0, end - self.preferences.span), min(max(end, self.preferences.span), duration))
        self.view_generation += 1
        self.last_plot_request = None

    def _show_entire(self):
        self.follow.setChecked(False)
        self.fit_whole = True
        self._set_wave_range(0, self._duration_frames() / RATE)
        self.view_generation += 1
        self.last_plot_request = None

    def _record_options(self):
        return RecordingOptions(self.preferences.recording_directory, device_names={"input_name": self.input_combo.currentText(), "output_name": self.output_combo.currentText()}) if self.preferences.auto_record else None

    def _studio_tick(self):
        self.player.poll()
        self.output_gain.setToolTip("历史试听音量" if self.historical else "监听音量")
        if self.player.error and self.player.error != self.last_play_error:
            self.last_play_error = self.player.error
            self.status_label.setText(f"历史播放失败：{self.player.error}")
        if self.historical:
            total = self.history_reader.frames
            self.play_button.setText("暂停" if self.player.playing else "播放")
            self.play_position.setText(clock_text(self.player.position / RATE))
            if not self.play_slider.isSliderDown():
                self.play_slider.setValue(round(self.player.position / max(1, total) * 1000000))
            self.cursor.setValue(self.player.position / RATE)
        else:
            total = self.engine.history.total
            rec = self.engine.recording_status
            labels = {"off": "未开始录音", "recording": "录音中", "complete": "会话已保存", "interrupted": "录音不完整", "error": "录音失败"}
            self.recording_label.setText(f"{labels.get(rec['status'], rec['status'])}  {clock_text(rec['frames'] / RATE)}" + (f"\n{rec['error']}" if rec['error'] else ""))
            self.recording_label.setToolTip(rec["folder"])
            if rec["error"] and rec["error"] != self.record_error:
                self.record_error = rec["error"]
                self.status_label.setText(("录音已停止，实时处理继续：" if self.engine.running else "录音保存失败：") + rec["error"])
        duration = max(0.1, total / RATE) if total else self.preferences.span
        earliest = 0
        if not self.historical and not self.engine.recording_status["frames"]:
            earliest = max(0, total - self.engine.history.capacity) / RATE
        self.programmatic_view = True
        try:
            self.wave.setLimits(xMin=earliest, xMax=duration, minXRange=0.1, maxXRange=max(0.1, duration - earliest))
        finally:
            self.programmatic_view = False
        if self.fit_whole:
            self._set_wave_range(earliest, duration)
        elif self.follow.isChecked():
            end = self.player.position / RATE if self.historical else total / RATE
            self._set_wave_range(max(0, end - self.preferences.span), (max(0.1, end) if total else self.preferences.span) if not self.historical else max(end, min(self.preferences.span, duration)))
        result = self.browser.poll()
        if self.browser.error:
            if self.plot_error != self.browser.error:
                self.plot_error = self.browser.error
                self.status_label.setText(f"波形读取暂未完成：{self.browser.error}")
            if time.monotonic() >= self.plot_retry_at:
                self.last_plot_request = None
                self.plot_retry_at = time.monotonic() + 1
        if result is not None:
            x, values, frequency, spectrum = result
            for col, curve in enumerate((self.raw_curve, self.clean_curve)):
                curve.setData(x, values[:, col], connect="finite")
            for col, curve in enumerate((self.raw_spectrum, self.clean_spectrum)):
                curve.setData(frequency, spectrum[:, col])
            if len(x) or len(frequency):
                for label in self.empty_plot_labels:
                    label.hide()
        if total:
            start, stop = self.wave.viewRange()[0]
            pixels = max(128, int(self.wave.plotItem.vb.width()))
            spectrum_at = self.player.position if self.historical else (total if self.follow.isChecked() or self.fit_whole else min(total, round(stop * RATE)))
            key = (self.audio_generation, self.view_generation)
            observed = total if self.follow.isChecked() or self.fit_whole else min(total, round(stop * RATE))
            signature = (key, observed, round(start, 5), round(stop, 5), pixels, spectrum_at)
            if signature != self.last_plot_request:
                folder = self.history_reader.folder if self.historical else self.engine.recording_status["folder"]
                self.browser.request(key, None if self.historical else self.engine.history, folder,
                                     round(start * RATE), round(stop * RATE), pixels, spectrum_at)
                self.last_plot_request = signature

    def _update_studio_actions(self):
        if not hasattr(self, "session_button"):
            return
        running = self.engine.running or self.engine.stream is not None
        self.session_button.setEnabled(not running and self.future is None)
        if self.historical:
            self.start_action.setEnabled(False)
            self.export_action.setEnabled(False)
            for index in range(3):
                self.control_tabs.setTabEnabled(index, False)
            self.monitor.setEnabled(False)
            self.identify.setEnabled(False)
            self.members_button.setEnabled(False)
            self.input_combo.setEnabled(False)
            self.output_combo.setEnabled(not self.player.playing)
            self.metrics.setText("历史试听")
            self.state_badge.setText("历史会话")
        else:
            for index in range(3):
                self.control_tabs.setTabEnabled(index, True)
            self.monitor.setEnabled(True)
            self.identify.setEnabled(True)
            self.members_button.setEnabled(True)

    def _open_sessions(self):
        if self.engine.running or self.engine.stream is not None:
            self._error("请先停止采集，再打开历史会话")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("历史会话")
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        rows = list_sessions(self.preferences.recording_directory)
        last_folder = self.engine.recording_status["folder"]
        if last_folder and Path(last_folder).is_dir() and all(path.resolve() != Path(last_folder).resolve() for path, _ in rows):
            try:
                rows.insert(0, (Path(last_folder), SessionReader(last_folder).meta))
            except (OSError, ValueError):
                pass
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["保存时间", "时长", "状态"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        labels = {"recording": "录音中", "complete": "完整", "interrupted": "不完整", "error": "录音失败"}
        for row, (folder, meta) in enumerate(rows):
            for col, value in enumerate((meta["started_at"][:19].replace("T", " "), clock_text(meta["frames"] / RATE), labels.get(meta["status"], meta["status"]))):
                item = QTableWidgetItem(value)
                item.setToolTip(str(folder) + ("\n" + meta.get("error", "") if meta.get("error") else ""))
                table.setItem(row, col, item)
        layout.addWidget(table)
        if not rows:
            layout.addWidget(QLabel("当前目录暂无会话，可在设置中启用完整会话保存。"))
        buttons = QHBoxLayout()
        open_button, folder_button, close_button = QPushButton("打开会话"), QPushButton("打开目录"), QPushButton("关闭")
        for button in (open_button, folder_button, close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        def selected():
            return table.currentRow()
        def update():
            index = selected()
            folder_button.setEnabled(index >= 0)
            open_button.setEnabled(index >= 0 and rows[index][1]["frames"] > 0 and rows[index][1]["status"] != "recording")
        table.itemSelectionChanged.connect(update)
        if rows:
            table.setCurrentCell(0, 0)
        update()
        def open_selected():
            if selected() >= 0:
                try:
                    self._load_session(rows[selected()][0])
                    dialog.accept()
                except Exception as exc:
                    QMessageBox.information(dialog, "无法打开会话", str(exc))
        open_button.clicked.connect(open_selected)
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(rows[selected()][0]))) if selected() >= 0 else None)
        close_button.clicked.connect(dialog.reject)
        table.doubleClicked.connect(lambda _: open_selected())
        self.session_dialog = dialog
        dialog.exec()

    def _load_session(self, folder):
        if self.engine.running or self.engine.stream is not None:
            raise ValueError("请先停止实时采集")
        reader = SessionReader(folder)
        if reader.meta["status"] == "recording":
            raise ValueError("此会话仍在录音，请先停止录音")
        if not reader.frames:
            raise ValueError("此会话没有可读取的音频")
        reader.read_range(0, min(reader.frames, 1))
        self.player.stop()
        self.player.position = 0
        self.player.error = ""
        self.history_reader = reader
        self.audio_generation += 1
        self._reset_observation()
        self.input_combo.hide()
        self.source_caption.setText(reader.folder.name)
        self.source_caption.setToolTip(str(reader.folder))
        self.source_caption.show()
        self.input_label.setText("会话")
        self.follow.setText("跟随播放")
        self.return_live.show()
        self.playback_bar.show()
        self.play_duration.setText(clock_text(reader.frames / RATE))
        self.speaker_panel.hide()
        self.cursor.show()
        self.control_tabs.setCurrentIndex(3)
        self.status_label.setText("历史会话，只读回看与试听")
        self._show_entire()
        self._update_actions()

    def _return_to_live(self):
        self.player.stop()
        self.history_reader = None
        self.audio_generation += 1
        self.engine.history = History()
        self._reset_observation()
        self.source_caption.hide()
        self.input_combo.show()
        self.input_label.setText("输入")
        self.follow.setText("跟随实时")
        self.return_live.hide()
        self.playback_bar.hide()
        self.speaker_panel.show()
        self.cursor.hide()
        self._reset_view()
        self.status_label.setText("已返回实时模式，点击启动开始采集。")
        self._update_actions()

    def _play_pause(self):
        try:
            if self.engine.running or not self.historical:
                raise ValueError("请先停止实时采集并打开历史会话")
            if self.player.playing:
                self.player.pause()
            else:
                if self.player.position >= self.history_reader.frames:
                    self.player.seek(0)
                self.player.play(self.history_reader.folder, self.output_combo.currentData(), self.output_gain.value())
        except Exception as exc:
            self._error(exc)

    def _seek_playback(self):
        if self.historical:
            self.player.seek(round(self.play_slider.value() / 1000000 * self.history_reader.frames))
            if getattr(self, "seek_was_playing", False):
                self.player.play(self.history_reader.folder, self.output_combo.currentData(), self.output_gain.value())
            self.last_plot_request = None

    def _begin_playback_seek(self):
        self.seek_was_playing = self.player.playing
        self.player.pause()

    def _change_playback_track(self, *_):
        self.player.seek(self.player.position, self.play_track.currentData())
        self.last_plot_request = None

    def _output_device_changed(self, *_):
        if self.historical:
            self.player.stop()
