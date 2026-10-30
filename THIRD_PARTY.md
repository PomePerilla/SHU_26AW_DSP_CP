# 第三方依赖与来源

本项目算法实现为课程新增代码，没有把商业SDK或未接入的调研方案计作自主实现。依赖的完整实际版本记录在 `requirements-lock.txt`；不在此重新分发上游源码。

| 实际依赖 | 用途 | 上游 |
|---|---|---|
| NumPy / SciPy | 数值计算、FFT、窗、参考音频重采样 | https://numpy.org/ 、https://scipy.org/ |
| Numba / llvmlite | 编译NLMS和回声逐样本循环，在音频设备启动前预热 | https://numba.pydata.org/ |
| sounddevice / PortAudio | 本地音频双工I/O | https://python-sounddevice.readthedocs.io/ 、https://www.portaudio.com/ |
| soundfile / libsndfile | 本地音频读写 | https://python-soundfile.readthedocs.io/ |
| PySide6 Essentials / Qt | 桌面界面；未使用Qt WebEngine | https://doc.qt.io/qtforpython-6/ |
| pyqtgraph | 实时图形显示 | https://www.pyqtgraph.org/ |
| sherpa-onnx | 本地CPU声纹模型推理 | https://github.com/k2-fsa/sherpa-onnx |
| pytest（开发依赖） | 验证 | https://docs.pytest.org/ |

Qt for Python有LGPLv3/GPLv3和商业授权选项，其他依赖也有各自的许可和二进制依赖通知。当前为本地课程开发环境；如果后续制作安装包，应随包保留实际依赖的许可文件，不将本表替代许可原文。

## EtherScope 界面适配来源

`dsp_lab/window_frame.py` 的标题栏与Windows原生窗口处理改编自本机 EtherScope 2.3.0 的 `window_title_bar.cpp`、`main_window_frame.cpp`；主题采用其 `style.h` 与 `main_window_theme.cpp` 的配色、密度和控件规则。Copyright (c) 2026 EtherScope contributors，MIT许可原文见 `ui/reference-licenses/EtherScope-MIT.txt`。DSP Studio 使用自己的波形图标，不使用 EtherScope 的E形品牌图案。

## 声纹模型

- 模型：`3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`。
- 上游方法与代码：[modelscope/3D-Speaker](https://github.com/modelscope/3D-Speaker)，代码Apache-2.0。
- 原始模型卡：[ModelScope CAM++](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common)。本轮网页动态内容未能完整提取，不能据上游代码许可单独断言所有模型权重条款。
- 本次ONNX文件：[sherpa-onnx官方发布附件](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models)。该转换权重的来源、文件大小和本地SHA-256存于 `models/manifest.json`。发布接口未提供该模型的SHA-256，因此本地哈希用于追踪本次下载，不声称是发布者认证摘要。
- 不是本组训练的模型，报告必须标明“使用预训练CAM++模型”。后续分发权重时应进一步核对模型卡及权重分发条件。

## 公开验证音频

`validation/fixtures/` 中的音频来自同一个sherpa-onnx公开发布附件，用于接口验证；保留原文件名与 `manifest.json` 中的来源、发布者摘要。

这些声音**不属于项目组员**，没有写入正式 `data/profiles.json`，不会冒充组员注册或组员准确率。测试使用独立的临时档案目录，注册片段与测试片段分离。公开示例的获取不等于取得任意再分发用途的授权，课程提交包可排除验证音频。
