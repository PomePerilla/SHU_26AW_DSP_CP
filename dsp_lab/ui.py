from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import time
import numpy as np
import sounddevice as sd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QAction, QKeySequence, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QCheckBox, QDoubleSpinBox, QSpinBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QScrollArea, QSplitter, QFrame, QTabWidget, QSizePolicy, QDialog,
    QDialogButtonBox,
)
from .audio import load_wave
from .audio_process import AudioClient
from .core import RATE, HOP, dbfs, warm_up
from .speaker import SpeakerService
from .theme import apply_theme, outline_icon
from .window_frame import FrameWindow, WindowTitleBar
from .ui_controls import DeviceComboBox, AdaptiveToolbar
from .studio_ui import StudioFeatures

def note(text):
    label = QLabel(text)
    label.setProperty("wbRole", "muted")
    label.setWordWrap(True)
    return label


class ElidedLabel(QLabel):
    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self._fit()

    def _fit(self):
        super().setText(self.fontMetrics().elidedText(getattr(self, "full_text", ""), Qt.TextElideMode.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        self._fit()
        super().resizeEvent(event)


def section(layout, title):
    label = QLabel(title)
    label.setProperty("wbRole", "section")
    label.setFixedHeight(28)
    layout.addWidget(label)


def form_row(layout, title, control):
    row = QHBoxLayout()
    label = QLabel(title)
    label.setBuddy(control)
    row.addWidget(label, 1)
    control.setFixedWidth(112)
    control.setAlignment(Qt.AlignmentFlag.AlignRight)
    row.addWidget(control)
    layout.addLayout(row)


class MainWindow(StudioFeatures, FrameWindow):
    def __init__(self, root, test_mode=False):
        super().__init__()
        self.root = root
        self.setWindowTitle("SHU_26AW_DSP_CP")
        self.setMinimumSize(900, 580)
        self.theme_name = "dark"
        self.diagnostics = None
        self.ui_busy = False
        self.last_error = ""
        self.audio_generation = 0
        self.identify_generation = 0
        self.tokens, self.colors = apply_theme(root, self.theme_name)
        self.number_font = QFont(self.tokens["fonts"]["data"][0])
        self.number_font.setPixelSize(self.tokens["metrics"]["font_size"])
        self.number_font.setFeature(QFont.Tag.fromString("tnum"), 1)
        self._start_icon_key = None
        self.engine = AudioClient()
        self._init_studio(test_mode)
        self.service = SpeakerService(root)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speaker")
        self.future = None
        self.job = ""
        self.model_ready = False
        self.prepared = False
        self.next_identify = 0.0
        self.enrollment_start = None
        self.enrollment_name = ""
        self.last_identified_at = 0.0
        self.input_devices = []
        self.output_devices = []
        self._build()
        self._setup_graphs()
        self._apply_theme()
        available = self.screen().availableGeometry()
        self.resize(min(1280, available.width() - 40), min(800, available.height() - 60))
        self._devices()
        self._update_actions()
        self.future = self.pool.submit(self._prepare)
        self.job = "prepare"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(120)

    def _prepare(self):
        warm_up()
        try:
            return {"speaker": self.service.load(), "error": ""}
        except Exception as exc:
            return {"speaker": None, "error": str(exc)}

    def _build(self):
        body = QWidget()
        body.setProperty("wbRole", "workspace")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        outer.addWidget(self.title_bar)
        devices = QWidget()
        source_row = QHBoxLayout(devices)
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(8)
        actions = QWidget()
        header = QHBoxLayout(actions)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.input_combo, self.output_combo = DeviceComboBox(), DeviceComboBox()
        for combo, name, label in ((self.input_combo, "麦克风", "输入"), (self.output_combo, "输出设备", "输出")):
            combo.setAccessibleName(name)
            combo.setMinimumWidth(100)
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(DeviceComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(lambda _, c=combo: c.setToolTip(c.currentText()))
            source_label = QLabel(label)
            source_label.setBuddy(combo)
            if combo is self.input_combo:
                self.input_label = source_label
            source_row.addWidget(source_label)
            source_row.addWidget(combo, 1)
            if combo is self.input_combo:
                self.source_caption = ElidedLabel()
                self.source_caption.setMinimumWidth(0)
                self.source_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                source_row.addWidget(self.source_caption, 1)
                self.source_caption.hide()
        self.output_combo.currentIndexChanged.connect(self._output_device_changed)
        self.state_badge = note("● 准备中")
        self.state_badge.setObjectName("statusBadge")
        self.state_badge.setProperty("wbRole", "")
        self.state_badge.setWordWrap(False)
        self.start_action = QAction("准备中…", self)
        self.start_action.setShortcut(QKeySequence("Ctrl+Return"))
        self.start_action.triggered.connect(self._start_stop)
        self.export_action = QAction("导出最近30秒", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+S"))
        self.export_action.triggered.connect(self._export)
        self.addActions([self.start_action, self.export_action])
        self.start_button = QPushButton()
        self.start_button.setProperty("wbRole", "primary")
        self.start_button.setMinimumWidth(100)
        self.start_button.setToolTip("启动或停止连续处理  Ctrl+Enter")
        self.start_button.clicked.connect(self.start_action.trigger)
        self.export_button = QPushButton()
        self.export_button.setMinimumWidth(132)
        self.export_button.setProperty("wbRole", "secondary")
        self.export_button.setToolTip("导出最近30秒的四路音频  Ctrl+S")
        self.export_button.clicked.connect(self.export_action.trigger)
        for action, button in ((self.start_action, self.start_button), (self.export_action, self.export_button)):
            def sync(a=action, b=button):
                b.setText(a.text())
                b.setEnabled(a.isEnabled())
            action.changed.connect(sync)
            sync()
        header.addWidget(self.start_button)
        header.addWidget(self.export_button)
        self.session_button = QPushButton("会话")
        self.session_button.setProperty("wbRole", "ghost")
        self.session_button.clicked.connect(self._open_sessions)
        header.addWidget(self.session_button)
        header.addStretch()
        self.theme_button = QPushButton()
        self.theme_button.setProperty("wbRole", "ghost")
        self.theme_button.setFixedWidth(30)
        self.theme_button.setToolTip("切换深色或浅色外观，不中断音频")
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        self.help_button = QPushButton()
        self.help_button.setProperty("wbRole", "ghost")
        self.help_button.setFixedWidth(30)
        self.help_button.setAccessibleName("使用说明")
        self.help_button.setToolTip("打开使用说明")
        self.help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / "README.md"))))
        header.addWidget(self.help_button)
        self.toolbar = AdaptiveToolbar(devices, actions, self.tokens["presentation"]["toolbar_breakpoint"])
        outer.addWidget(self.toolbar)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(5)
        self.control_tabs = QTabWidget()
        self.control_tabs.setMinimumWidth(260)
        self.control_tabs.setMaximumWidth(340)
        self.control_tabs.setDocumentMode(True)
        self.control_tabs.tabBar().setExpanding(True)
        self.control_tabs.setAccessibleName("处理控制")
        self.split.addWidget(self.control_tabs)

        def control_page(title):
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(8, 4, 8, 8)
            layout.setSpacing(8)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            self.control_tabs.addTab(scroll, title)
            return layout

        effects = control_page("音效")
        section(effects, "语音增强")
        self.enhance = QCheckBox("频谱降噪")
        self.enhance.setChecked(True)
        effects.addWidget(self.enhance)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0.1, 3)
        self.strength.setSingleStep(0.1)
        self.strength.setValue(1.2)
        self.strength.setToolTip("提高抑制强度可降低更多噪声，也可能损伤语音")
        form_row(effects, "抑制强度", self.strength)
        self.noise_button = QPushButton("学习环境噪声  1秒")
        self.noise_button.setProperty("wbRole", "secondary")
        self.noise_button.clicked.connect(self._learn_noise)
        effects.addWidget(self.noise_button)
        self.noise_label = note("启动后保持安静，学习1秒噪声。")
        effects.addWidget(self.noise_label)
        section(effects, "数字回声")
        self.echo = QCheckBox("产生回声")
        self.inverse_echo = QCheckBox("消除生成的回声")
        self.inverse_echo.setToolTip("仅消除本系统产生的已知延时回声")
        effects.addWidget(self.echo)
        effects.addWidget(self.inverse_echo)
        self.echo_delay = QSpinBox()
        self.echo_delay.setRange(20, 600)
        self.echo_delay.setValue(180)
        self.echo_delay.setSuffix(" ms")
        self.echo_gain = QDoubleSpinBox()
        self.echo_gain.setRange(0, 0.85)
        self.echo_gain.setValue(0.45)
        self.echo_gain.setSingleStep(0.05)
        form_row(effects, "回声延时", self.echo_delay)
        form_row(effects, "回声比例", self.echo_gain)
        effects.addStretch()

        acoustic = control_page("声学回声")
        section(acoustic, "参考式回声消除")
        self.aec = QCheckBox("启用声学回声消除")
        self.adapt = QCheckBox("学习回声路径")
        self.adapt.setChecked(True)
        acoustic.addWidget(self.aec)
        acoustic.addWidget(self.adapt)
        self.load_reference_button = QPushButton("选择参考音频…")
        self.load_reference_button.setProperty("wbRole", "secondary")
        self.load_reference_button.clicked.connect(self._load_reference)
        acoustic.addWidget(self.load_reference_button)
        self.reference_label = ElidedLabel()
        self.reference_label.setText("尚未选择参考音频")
        self.reference_label.setProperty("wbRole", "muted")
        self.reference_label.setMinimumWidth(0)
        self.reference_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reference_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        acoustic.addWidget(self.reference_label)
        self.play_reference = QCheckBox("循环播放参考音频")
        self.play_reference.toggled.connect(lambda value: setattr(self.engine, "reference_enabled", value))
        acoustic.addWidget(self.play_reference)
        self.reference_gain = QDoubleSpinBox()
        self.reference_gain.setRange(0, 0.8)
        self.reference_gain.setValue(0.2)
        self.reference_gain.setSingleStep(0.05)
        form_row(acoustic, "参考音量", self.reference_gain)
        self.reset_button = QPushButton("重置回声路径")
        self.reset_button.setProperty("wbRole", "secondary")
        self.reset_button.clicked.connect(lambda: self._set(reset_token=self.engine.settings.reset_token + 1))
        acoustic.addWidget(self.reset_button)
        acoustic.addWidget(note("先播放参考、保持安静学习数秒，再关闭「学习回声路径」并讲话。"))
        acoustic.addStretch()

        members = control_page("组员")
        section(members, "声纹注册")
        self.member_name = QLineEdit()
        self.member_name.setPlaceholderText("输入组员姓名")
        self.member_name.setAccessibleName("组员姓名")
        self.member_name.setMaxLength(30)
        members.addWidget(self.member_name)
        self.enroll_button = QPushButton("采集6秒并注册")
        self.enroll_button.setProperty("wbRole", "secondary")
        self.enroll_button.clicked.connect(self._enroll)
        members.addWidget(self.enroll_button)
        members.addWidget(note("每人用不同句子注册3次，再用新句子测试。"))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.1, 0.95)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.55)
        self.threshold.setToolTip("余弦相似度阈值；还须满足前两名差值≥0.05，需按组员录音校准")
        form_row(members, "识别阈值", self.threshold)
        section(members, "已注册组员")
        self.profile_hint = note("暂无组员，请先启动并注册。")
        members.addWidget(self.profile_hint)
        self.speaker_table = QTableWidget(0, 3)
        self.speaker_table.setHorizontalHeaderLabels(["姓名", "次数", "相似度"])
        self.speaker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.speaker_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.speaker_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.speaker_table.verticalHeader().setVisible(False)
        self.speaker_table.verticalHeader().setDefaultSectionSize(self.tokens["metrics"]["row_height"])
        self.speaker_table.setAlternatingRowColors(True)
        self.speaker_table.setShowGrid(False)
        self.speaker_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.speaker_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.speaker_table.setMinimumHeight(100)
        members.addWidget(self.speaker_table, 1)
        self._refresh_profiles()
        self._build_display_settings(control_page("设置"))

        display = QWidget()
        right = QVBoxLayout(display)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_plot_tools())
        self.chart_tabs = QTabWidget()
        self.chart_tabs.setDocumentMode(True)
        self.chart_tabs.setAccessibleName("信号观察")
        self.wave = pg.PlotWidget()
        self.wave.setMinimumSize(300, 160)
        self.wave.setYRange(-1, 1, padding=0)
        self.wave.setXRange(0, self.preferences.span, padding=0)
        self.wave.enableAutoRange(x=False, y=False)
        self.wave.setLabel("left", "幅度")
        self.wave.setLabel("bottom", "时间", units="s")
        self.wave.showGrid(x=False, y=True, alpha=0.1)
        self.wave.addLegend(offset=(10, 8))
        self.wave.getAxis("left").setTickSpacing(levels=[(0.5, 0)])
        self.wave.getAxis("bottom").setStyle(maxTickLevel=0)
        self.raw_curve = self.wave.plot(name="原始")
        self.clean_curve = self.wave.plot(name="处理后")
        self.chart_tabs.addTab(self.wave, "波形")
        self.spectrum = pg.PlotWidget()
        self.spectrum.setMinimumSize(300, 160)
        self.spectrum.setXRange(0, RATE // 2, padding=0)
        self.spectrum.setYRange(-100, 0, padding=0)
        self.spectrum.enableAutoRange(x=False, y=False)
        self.spectrum.setLabel("bottom", "频率", units="Hz")
        self.spectrum.setLabel("left", "幅度", units="dBFS")
        self.spectrum.showGrid(x=False, y=True, alpha=0.1)
        self.spectrum.addLegend(offset=(10, 8))
        self.spectrum.getAxis("left").setTickSpacing(levels=[(20, 0)])
        self.spectrum.getAxis("bottom").setTickSpacing(levels=[(2000, 0)])
        self.raw_spectrum = self.spectrum.plot(name="原始")
        self.clean_spectrum = self.spectrum.plot(name="处理后")
        self.chart_tabs.addTab(self.spectrum, "频谱 FFT")
        self.chart_proxies = []
        for plot in (self.wave, self.spectrum):
            self.chart_proxies.append(pg.SignalProxy(
                plot.scene().sigMouseMoved, rateLimit=20,
                slot=lambda event, p=plot: self._show_plot_value(p, event[0]),
            ))
        self.empty_plot_labels = []
        for plot, description in ((self.wave, "波形"), (self.spectrum, "频谱")):
            empty = QLabel(f"等待麦克风输入\n启动处理后显示实时{description}", plot)
            empty.setObjectName("emptyPlot")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.empty_plot_labels.append(empty)
        for plot in (self.wave, self.spectrum):
            plot.plotItem.vb.sigResized.connect(self._position_empty_labels)
        right.addWidget(self.chart_tabs, 1)
        right.addWidget(self._build_playback_bar())
        speaker_panel = QFrame()
        self.speaker_panel = speaker_panel
        speaker_panel.setObjectName("speakerPanel")
        speaker_panel.setProperty("wbRole", "surface")
        summary = QVBoxLayout(speaker_panel)
        summary.setContentsMargins(8, 6, 8, 6)
        row = QHBoxLayout()
        self.speaker_label = ElidedLabel()
        self.speaker_label.setText("准备声纹模型…")
        self.speaker_label.setObjectName("speaker")
        self.speaker_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.speaker_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.speaker_label, 1)
        self.identify = QCheckBox("连续识别")
        self.identify.setChecked(True)
        row.addWidget(self.identify)
        self.members_button = QPushButton("管理组员")
        self.members_button.setProperty("wbRole", "ghost")
        self.members_button.clicked.connect(lambda: self.control_tabs.setCurrentIndex(2))
        row.addWidget(self.members_button)
        summary.addLayout(row)
        self.speaker_detail = note("")
        self.speaker_detail.setToolTip("相似度不是概率；模型与阈值需要用真实组员录音验证")
        summary.addWidget(self.speaker_detail)
        self.speaker_detail.hide()
        right.addWidget(speaker_panel)
        self.split.addWidget(display)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([288, 900])
        outer.addWidget(self.split, 1)

        output_bar = QFrame()
        output_bar.setObjectName("outputBar")
        output = QHBoxLayout(output_bar)
        output.setContentsMargins(8, 4, 8, 4)
        output.setSpacing(8)
        self.monitor = QCheckBox("监听输出")
        self.monitor.setToolTip("戴耳机试听；扬声器近距离监听容易形成啸叫")
        output.addWidget(self.monitor)
        self.output_gain = QDoubleSpinBox()
        self.output_gain.setRange(0, 1)
        self.output_gain.setValue(0.6)
        self.output_gain.setSingleStep(0.1)
        self.output_gain.setFixedWidth(82)
        self.output_gain.setAccessibleName("监听音量")
        output.addWidget(QLabel("音量"))
        output.addWidget(self.output_gain)
        output.addWidget(self.state_badge)
        self.status_label = ElidedLabel()
        self.status_label.setText("点击启动，开始连续采集。")
        self.status_label.setProperty("wbRole", "muted")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        output.addWidget(self.status_label, 1)
        self.metrics = note("16 kHz  未启动")
        self.metrics.setProperty("wbRole", "data")
        self.metrics.setFont(self.number_font)
        self.metrics.setWordWrap(False)
        output.addWidget(self.metrics)
        self.details_button = QPushButton("运行详情")
        self.details_button.setObjectName("detailsButton")
        self.details_button.setProperty("wbRole", "ghost")
        self.details_button.clicked.connect(self._show_diagnostics)
        output.addWidget(self.details_button)
        outer.addWidget(output_bar)
        self.setCentralWidget(body)
        for control in (self.strength, self.echo_delay, self.echo_gain, self.output_gain,
                        self.reference_gain, self.threshold):
            control.setFont(self.number_font)
            control.setAlignment(Qt.AlignmentFlag.AlignRight)
        for control in (self.enhance, self.echo, self.inverse_echo, self.aec, self.adapt, self.monitor):
            control.toggled.connect(self._sync_settings)
        for control in (self.strength, self.echo_delay, self.echo_gain, self.output_gain, self.reference_gain):
            control.valueChanged.connect(self._sync_settings)
        self._update_actions()

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._apply_theme()

    def _apply_theme(self):
        self.tokens, self.colors = apply_theme(self.root, self.theme_name)
        c = self.colors
        target = "浅色" if self.theme_name == "dark" else "深色"
        self.theme_button.setAccessibleName(f"切换至{target}外观")
        self.theme_button.setToolTip(f"切换至{target}外观，不中断音频")
        for button, name in ((self.export_button, "download"),
                             (self.theme_button, "sun" if self.theme_name == "dark" else "moon"),
                             (self.help_button, "help"), (self.members_button, "users"),
                             (self.details_button, "activity"), (self.noise_button, "noise"),
                             (self.load_reference_button, "folder"), (self.reset_button, "reset"),
                             (self.enroll_button, "mic")):
            button.setIcon(outline_icon(name, c["text"], c["disabled"]))
            button.setIconSize(QSize(20, 20))
        self._start_icon_key = None
        self._update_start_icon()
        tick_font = QFont(self.tokens["fonts"]["data"][0])
        tick_font.setPixelSize(12)
        tick_font.setFeature(QFont.Tag.fromString("tnum"), 1)
        for plot in (self.wave, self.spectrum):
            plot.setBackground(c["surface"])
            for side in ("left", "bottom"):
                axis = plot.getAxis(side)
                axis.setPen(c["border"])
                axis.setTextPen(c["muted"])
                axis.setStyle(tickFont=tick_font, tickLength=-3, maxTickLevel=0)
                axis.setLabel(axis.labelText, units=axis.labelUnits, color=c["muted"], **{"font-size": "12px", "font-family": "Microsoft YaHei UI"})
            legend = plot.plotItem.legend
            if legend:
                for _, label in legend.items:
                    label.setAttr("color", c["muted"])
                    label.setAttr("size", "12px")
                    label.setAttr("italic", False)
        for curve in (self.raw_curve, self.raw_spectrum):
            curve.setPen(pg.mkPen(c["chart_raw"], width=1, style=Qt.PenStyle.DashLine))
        for curve in (self.clean_curve, self.clean_spectrum):
            curve.setPen(pg.mkPen(c["chart_processed"], width=1.5))
        self.cursor.setPen(pg.mkPen(c["muted"], style=Qt.PenStyle.DotLine))
        self.title_bar.refresh()
        self.configure_frame()

    def _update_start_icon(self):
        running = self.engine.running or self.engine.stream is not None
        name = "stop" if running else "play"
        key = (name, self.theme_name)
        if key != self._start_icon_key:
            ink = self.colors["error"] if running else self.colors["primary_text"]
            self.start_button.setIcon(outline_icon(name, ink, self.colors["disabled"]))
            self.start_button.setIconSize(QSize(20, 20))
            self.start_button.setProperty("wbRunning", running)
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._start_icon_key = key

    def _set_speaker_detail(self, text):
        self.speaker_detail.setText(text)
        self.speaker_detail.setVisible(bool(text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "empty_plot_labels"):
            self._position_empty_labels()

    def _position_empty_labels(self):
        for label, plot in zip(self.empty_plot_labels, (self.wave, self.spectrum)):
            scene = plot.plotItem.vb.sceneBoundingRect()
            center = plot.mapFromScene(scene.center())
            width = max(180, min(330, int(scene.width()) - 16))
            label.setGeometry(round(center.x() - width / 2), round(center.y() - 38), width, 76)

    def _reset_observation(self):
        self.browser.invalidate()
        self.last_plot_request = None
        for curve in (self.raw_curve, self.clean_curve, self.raw_spectrum, self.clean_spectrum):
            curve.clear()
        for plot in (self.wave, self.spectrum):
            plot.setToolTip("")
            plot.setAccessibleDescription("")
        for label in self.empty_plot_labels:
            label.show()

    def _show_plot_value(self, plot, scene_position):
        if not plot.sceneBoundingRect().contains(scene_position):
            return
        curves = (self.raw_curve, self.clean_curve) if plot is self.wave else (self.raw_spectrum, self.clean_spectrum)
        position = plot.plotItem.vb.mapSceneToView(scene_position).x()
        x, raw = curves[0].getData()
        _, processed = curves[1].getData()
        if x is None or len(x) == 0 or processed is None:
            plot.setToolTip("等待麦克风输入，启动处理后显示真实数据")
            return
        gaps = np.flatnonzero(~np.isfinite(x))
        for gap in gaps:
            if 0 < gap < len(x) - 1 and x[gap - 1] < position < x[gap + 1]:
                plot.setToolTip("此区间音频未保存")
                return
        finite = np.isfinite(x) & np.isfinite(raw) & np.isfinite(processed)
        x, raw, processed = x[finite], raw[finite], processed[finite]
        if not len(x):
            return
        index = min(len(x) - 1, max(0, int(np.searchsorted(x, position))))
        if index > 0 and abs(x[index - 1] - position) < abs(x[index] - position):
            index -= 1
        if plot is self.wave:
            value = f"时间位置 {x[index]:.3f} s\n原始 {raw[index]:.4f}\n处理后 {processed[index]:.4f}\n概览视图保留分桶峰值"
        else:
            value = f"频率 {x[index]:.1f} Hz\n原始 {raw[index]:.1f} dBFS\n处理后 {processed[index]:.1f} dBFS"
        plot.setToolTip(value)
        plot.setAccessibleDescription(value)

    def _show_diagnostics(self):
        if self.diagnostics is None:
            self.diagnostics = QDialog(self)
            self.diagnostics.setWindowTitle("运行详情")
            self.diagnostics.setMinimumWidth(380)
            layout = QVBoxLayout(self.diagnostics)
            layout.setContentsMargins(20, 16, 20, 16)
            layout.setSpacing(16)
            grid = QGridLayout()
            grid.setHorizontalSpacing(32)
            grid.setVerticalSpacing(12)
            self.diagnostic_values = {}
            names = ("采样率", "帧长", "算法延迟", "总延迟估算", "每帧处理", "欠载", "处理超时", "削波块")
            for index, name in enumerate(names):
                label = note(name)
                value = QLabel("—")
                value.setProperty("wbRole", "data")
                value.setFont(self.number_font)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label, index, 0)
                grid.addWidget(value, index, 1)
                self.diagnostic_values[name] = value
            grid.setColumnStretch(0, 1)
            layout.addLayout(grid)
            layout.addWidget(note("延迟为设备报告值加算法延迟的估算，未做声学回环测量。"))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
            buttons.rejected.connect(self.diagnostics.close)
            layout.addWidget(buttons)
        self._update_diagnostics()
        self.diagnostics.show()
        self.diagnostics.raise_()

    def _update_diagnostics(self):
        if self.diagnostics is None:
            return
        stats = self.engine.stats
        latency = sum(self.engine.stream.latency) * 1000 + 16 if self.engine.stream else None
        delay_text = f"{latency:.0f} ms" if latency is not None else "未连接"
        values = {"采样率": "16 kHz / 单声道", "帧长": "16 ms", "算法延迟": "16 ms", "总延迟估算": delay_text}
        for name, key, suffix in (("每帧处理", "processing_ms", " ms"), ("欠载", "xruns", ""),
                                  ("处理超时", "deadline_misses", ""), ("削波块", "clipped_blocks", "")):
            value = stats.get(key)
            values[name] = "—" if value is None else (f"{value:.2f}" if key == "processing_ms" else str(value)) + suffix
        for name, value in values.items():
            self.diagnostic_values[name].setText(value)
        if self.historical:
            for name, value in (("总延迟估算", "历史播放"), ("每帧处理", "—"), ("欠载", str(self.player.underruns)), ("处理超时", "—"), ("削波块", "—")):
                self.diagnostic_values[name].setText(value)

    def _update_actions(self):
        self._update_start_icon()
        running = self.engine.running
        connected = self.input_combo.count() > 0 and self.output_combo.count() > 0
        self.start_action.setText("停止处理" if running or self.engine.stream is not None else "启动处理")
        if not self.prepared:
            self.start_action.setText("准备中…")
        self.start_action.setEnabled(self.prepared and connected and not self.ui_busy)
        self.export_action.setEnabled(self.engine.history.total > 0)
        self.noise_button.setEnabled(running)
        self.enroll_button.setEnabled(running and self.model_ready and self.future is None and self.enrollment_start is None)
        self.input_combo.setEnabled(not running and self.engine.stream is None and not self.ui_busy)
        self.output_combo.setEnabled(not running and self.engine.stream is None and not self.ui_busy)
        self.inverse_echo.setEnabled(self.echo.isChecked())
        self.echo_delay.setEnabled(self.echo.isChecked())
        self.echo_gain.setEnabled(self.echo.isChecked())
        self.adapt.setEnabled(self.aec.isChecked())
        self.reset_button.setEnabled(running and self.aec.isChecked())
        self.play_reference.setEnabled(running and bool(len(self.engine.reference_audio)))
        if self.engine.error or self.last_error:
            text, tone = "● 需要处理", "error"
        elif running:
            text, tone = "● 运行中", "success"
        elif self.prepared:
            text, tone = "● 已就绪", "success"
        else:
            text, tone = "● 准备中", "warning"
        self.state_badge.setText(text)
        if self.state_badge.property("wbTone") != tone:
            self.state_badge.setProperty("wbTone", tone)
            self.state_badge.style().unpolish(self.state_badge)
            self.state_badge.style().polish(self.state_badge)
        self._update_studio_actions()

    def _devices(self):
        try:
            devices = sd.query_devices()
            hosts = sd.query_hostapis()
            for index, device in enumerate(devices):
                label = f"{device['name']} [{hosts[device['hostapi']]['name']}]"
                if device["max_input_channels"]:
                    self.input_combo.addItem(label, index)
                if device["max_output_channels"]:
                    self.output_combo.addItem(label, index)
            for combo in (self.input_combo, self.output_combo):
                for index in range(combo.count()):
                    combo.setItemData(index, combo.itemText(index), Qt.ItemDataRole.ToolTipRole)
            for combo, default in ((self.input_combo, sd.default.device[0]), (self.output_combo, sd.default.device[1])):
                found = combo.findData(default)
                if found >= 0:
                    combo.setCurrentIndex(found)
            if not self.input_combo.count() or not self.output_combo.count():
                self.status_label.setText("未找到输入或输出设备，请连接麦克风和耳机后重启。")
        except Exception as exc:
            self.status_label.setText(f"音频设备查询失败：{exc}")

    def _set(self, **kwargs):
        self.engine.settings = replace(self.engine.settings, **kwargs)

    def _sync_settings(self, *_):
        self.player.set_gain(self.output_gain.value())
        self._set(
            enhance=self.enhance.isChecked(), strength=self.strength.value(),
            echo=self.echo.isChecked(), inverse_echo=self.inverse_echo.isChecked(),
            echo_ms=self.echo_delay.value(), echo_gain=self.echo_gain.value(),
            aec=self.aec.isChecked(), adapt=self.adapt.isChecked(), monitor=self.monitor.isChecked(),
            output_gain=self.output_gain.value(), reference_gain=self.reference_gain.value(),
        )
        self._update_actions()

    def _start_stop(self):
        if self.historical:
            self._error("请先返回实时模式")
            return
        if self.engine.running or self.engine.stream is not None:
            self.audio_generation += 1
            self.engine.stop()
            self.play_reference.setChecked(False)
            self.enrollment_start = None
            self.enroll_button.setText("采集6秒并注册")
            self.status_label.setText("已停止采集，最近音频可导出。")
            self.metrics.setText("16 kHz  已停止")
            self._update_actions()
            return
        self.ui_busy = True
        self.last_error = ""
        self._update_actions()
        try:
            inp, out = self.input_combo.currentData(), self.output_combo.currentData()
            if inp is None or out is None:
                raise ValueError("请先连接并选择输入、输出设备")
            if sd.query_devices(inp)["hostapi"] != sd.query_devices(out)["hostapi"]:
                raise ValueError("输入、输出请选相同的音频接口类型，例如都选择 WASAPI 或都选择 MME")
            options = self._record_options()
            if options is None:
                self.engine.start(inp, out)
            else:
                self.engine.start(inp, out, recording=options)
            self.audio_generation += 1
            self._reset_observation()
            self.record_error = ""
            self._reset_view()
            self.noise_label.setText("保持安静，学习1秒环境噪声。")
            self.speaker_label.setText("等待组员语音" if self.service.profiles else "尚未注册组员")
            self._set_speaker_detail("")
            self._refresh_profiles()
            self.status_label.setText("连续采集中  学习噪声后讲话，戴耳机开启监听。")
        except Exception as exc:
            self._error(exc)
        finally:
            self.ui_busy = False
            self._update_actions()

    def _learn_noise(self):
        if not self.engine.running:
            self._error("请先启动实时处理")
            return
        self._set(learn_token=self.engine.settings.learn_token + 1)
        self.noise_label.setText("采集中，请保持安静1秒…")

    def _load_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择远端播放参考", str(self.root), "音频 (*.wav *.flac *.ogg)")
        if not path:
            return
        try:
            data = load_wave(path)
            self.engine.set_reference(data)
            from pathlib import Path
            self.reference_label.setText(f"{Path(path).name}  {len(data) / RATE:.1f}秒")
            self.reference_label.setToolTip(path)
            self._update_actions()
        except Exception as exc:
            self._error(exc)

    def _enroll(self):
        if not self.engine.running or not self.model_ready:
            self._error("请先启动实时音频，并等待声纹模型加载完成")
            return
        name = self.member_name.text().strip()
        if not name:
            self._error("请先输入组员姓名")
            return
        if self.future is not None:
            self._error("声纹任务正在完成，请稍后再试")
            return
        self.enrollment_name = name
        self.enrollment_start = self.engine.history.total
        self.enroll_button.setEnabled(False)
        self.speaker_label.setText("请连续说话6秒")
        self._set_speaker_detail("每人建议注册3次，使用不同句子；不要播放参考音频或多人同时讲话。")

    def _refresh_profiles(self, rankings=None):
        scores = {row["name"]: row["similarity"] for row in rankings or []}
        selected = {self.speaker_table.item(index.row(), 0).text()
                    for index in self.speaker_table.selectionModel().selectedRows()
                    if self.speaker_table.item(index.row(), 0)}
        scroll = self.speaker_table.verticalScrollBar().value()
        self.speaker_table.setRowCount(len(self.service.profiles))
        for row, (name, profile) in enumerate(self.service.profiles.items()):
            values = (name, str(profile["count"]), f"{scores[name]:.3f}" if name in scores else "—")
            for col, value in enumerate(values):
                item = self.speaker_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(value)
                    self.speaker_table.setItem(row, col, item)
                elif item.text() != value:
                    item.setText(value)
                item.setToolTip(value)
                if col > 0:
                    item.setFont(self.number_font)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if name in selected:
                self.speaker_table.selectRow(row)
        self.speaker_table.verticalScrollBar().setValue(scroll)
        count = len(self.service.profiles)
        self.profile_hint.setText(f"已注册 {count} 位组员" if count else "暂无组员，请先启动并注册。")
        self.speaker_table.setVisible(bool(count))

    def _tick(self):
        now = time.monotonic()
        self.engine.poll()
        if self.future is not None and self.future.done():
            future, job = self.future, self.job
            self.future = None
            try:
                result = future.result()
                if job == "prepare":
                    self.prepared = True
                    self.model_ready = bool(result["speaker"])
                    ready_text = "等待组员语音" if self.service.profiles else "尚未注册组员"
                    self.speaker_label.setText(ready_text if self.model_ready else "声纹模型未就绪")
                    if result["error"]:
                        self._set_speaker_detail(result["error"])
                elif job == "enroll":
                    self.speaker_label.setText(f"已注册：{result['name']}")
                    self._set_speaker_detail(f"此成员已采集 {result['count']} 次。请用不同句子测试识别。")
                    self._refresh_profiles()
                    self.next_identify = now + 2
                elif job == "identify" and self.identify_generation == self.audio_generation:
                    self.last_identified_at = now
                    self.speaker_label.setText(result["name"])
                    if "similarity" in result:
                        self._set_speaker_detail(f"余弦相似度 {result['similarity']:.3f}  与第二名差距 {result['margin']:.3f}")
                    self._refresh_profiles(result["rankings"])
            except Exception as exc:
                self.speaker_label.setText("等待有效语音" if job == "identify" else "声纹任务未完成")
                self._set_speaker_detail(str(exc))
        running = self.engine.running
        self._update_actions()
        self._update_diagnostics()
        self._studio_tick()
        if self.engine.error:
            self.status_label.setText(self.engine.error)
        if not running:
            return
        stats = self.engine.stats
        processing_ms = stats.get("processing_ms")
        self.metrics.setText("16 kHz  — ms/帧" if processing_ms is None else f"16 kHz  {processing_ms:.1f} ms/帧")
        self.metrics.setToolTip("延迟使用音频设备报告值加16 ms算法延迟，未进行声学回环实测；处理耗时与声纹识别异步。")
        if stats.get("noise_learning"):
            self.noise_label.setText("正在学习环境噪声，请保持安静…")
        elif stats.get("noise_profile"):
            self.noise_label.setText("噪声已学习  环境变化后可重新采集")
        try:
            data = self.engine.history.snapshot(3)
        except RuntimeError:
            return
        if self.enrollment_start is not None:
            elapsed = (self.engine.history.total - self.enrollment_start) / RATE
            self.enroll_button.setText(f"采集中 {min(6, elapsed):.1f} / 6秒")
            if elapsed >= 6 and self.future is None:
                try:
                    samples = self.engine.history.snapshot(6)[:, 0]
                    self.future = self.pool.submit(self.service.enroll, self.enrollment_name, samples)
                    self.job = "enroll"
                    self.enrollment_start = None
                    self.enroll_button.setText("采集6秒并注册")
                except Exception as exc:
                    self.enrollment_start = None
                    self._error(exc)
            return
        if self.identify.isChecked() and self.model_ready and self.service.profiles and self.future is None and now >= self.next_identify:
            self.next_identify = now + 2
            if len(data) >= RATE * 3 and dbfs(data[-RATE:, 0]) > -45:
                self.future = self.pool.submit(self.service.identify, data[:, 0].copy(), self.threshold.value())
                self.job = "identify"
                self.identify_generation = self.audio_generation
            else:
                self.speaker_label.setText("等待组员语音")
                self._refresh_profiles()

    def _export(self):
        try:
            folder = self.root / "data" / "sessions" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.engine.export(folder)
            self.status_label.setText(f"已导出最近音频：{folder}")
        except Exception as exc:
            self._error(exc)

    def _error(self, error):
        self.last_error = str(error)
        self.status_label.setText(str(error))
        self._update_actions()
        QMessageBox.information(self, "操作提示", str(error))

    def closeEvent(self, event):
        self.timer.stop()
        self.browser.close()
        self.player.stop()
        self.engine.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        super().closeEvent(event)
