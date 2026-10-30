# EtherScope 风格适配与验证

日期：2026-10-29。启动入口：`launch.cmd`。已打开的旧窗口需重启以载入新版。

## 修改与参考

按用户指定的 `D:/PRJ/20261012_计算机网络/output/EtherScope-2.3.0-preview/` 复刻工作台风格。只读核对预览包、对应源码和已有2.3.0截图，未修改参考项目。主题及标题栏源码与该项目2.3.0源码压缩包内版本一致；预览可执行文件哈希见 `ui/etherscope-contract.md`。

- 应用EtherScope的深灰/浅灰配色、黄色主操作与焦点、透明次操作、等宽数字、30px控件、细线分区和8px间距。
- 设备输入、输出和主动作合并到顶部工具条，减少外围空白；页签与面板边缘对齐，底部统一监听、状态、运行数据与详情入口。
- 采用32px自绘标题栏和44px窗口按钮，连接Windows原生拖动、边缘缩放、最大化工作区处理。窗口标题仍为DSP Studio，品牌图标为自有波形。
- 延续用户此前要求的直角组件，以及识别区精简文案。没有恢复窗口时长和更新频率的常驻小字。
- 所有音频和声纹服务继续使用既有接口。`core.py`、`audio.py`、`audio_process.py`、`speaker.py`及两份依赖文件与本轮开始时SHA256相同。

窗口代码改编自EtherScope的MIT源码，许可原文已保留，见 `THIRD_PARTY.md`。当前生成主题为 `ui/generated-etherscope-v1/`，Qt适配统一直角；管线基础token的1px半径仅用于满足原schema。

## 检查结果

| 检查 | 结果 |
|---|---|
| 主题生成、配置、文字对比度 | 14文件一致，20组对比度通过 |
| 界面布局与交互 | 100% / 150% / 200%各65条断言通过 |
| 尺寸与主题 | 900×580、1024×768、1440×900，深浅主题 |
| 原生Windows窗口 | 14条检查通过，实际设备比例2.0 |
| DSP回归 | 8 passed，1.14秒 |
| Python编译检查 | 通过 |
| Qt绘图+后台声纹+真实麦克风 | 25.047秒，13次声纹任务，欠载0、超时0、削波块0 |

离屏检查加载本机Segoe UI、微软雅黑和Consolas字体，覆盖主操作可见、按钮文字可容纳、图表/结果与监听可见、换肤保留参数和页签、组员表格选择身份、详情Esc关闭、受控启动/停止、导出条件、真实数据提示等。

原生窗口检查通过Qt测试操作本程序的窗口控件，并向本窗口发送Windows命中查询；没有操作其他程序。覆盖标题拖动区域、按钮与输入框不误判为拖动、左上缩放边缘、最大化/还原、标题双击、最小化、主题切换和关闭任务收尾。最大化后物理客户区为 `[0,0,3072,1824]`，与系统工作区相同；外层隐藏缩放边框越出工作区是Windows原生行为，不能用外层边框尺寸替代可见客户区验收。初次检查误以外框比较，随后已改为核对客户区并通过。

已人工查看深色主工作区、浅色900最小窗口、声学回声与频谱页、组员表格、运行详情，以及Windows原生深浅窗口截图。最小窗口设备名会省略，提示中保留全文；实际音频设置使用设备ID。

实时联测输出静音，不保存麦克风原始录音，不产生组员档案；最高周期采样帧处理耗时0.7672ms。该值是周期观察峰值，不能等同于全部回调的最大值、端到端声学延迟或长时间稳定性。

## 证据与使用

`validation/etherscope-ui/` 保存改前截图、三档布局检查、改后截图、`native-frame.json`、原生窗口截图、`gui-live.json`和汇总。带 `controlled` 的截图是显式受控状态，仅用于UI验证，不代表实际组员识别结果。

```powershell
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe C:\Users\xande\.codex\skills\workbench-ui-pipeline\scripts\check.py --config ui/project-contract.json --generated ui/generated-etherscope-v1
$env:QT_QPA_PLATFORM='offscreen'
$env:QT_SCALE_FACTOR='1' # 另外以1.5、2分别启动独立进程
.venv\Scripts\python.exe tools/verify_frontend.py --scale 1
# 原生检查需在新终端运行，或先清除QT_QPA_PLATFORM与QT_SCALE_FACTOR。
.venv\Scripts\python.exe tools/verify_window_frame.py
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools/validate_gui.py
```

原生跨显示器混合DPI拖动、Windows贴靠操作、读屏器和非Windows窗口行为未实测。本轮没有新增听感、房间AEC、真实组员准确率或长时间稳定性验收。没有生成发布包。
