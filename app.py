import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    from dsp_lab.ui import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("DSP Studio")
    app.setStyle("Fusion")
    try:
        window = MainWindow(ROOT)
        window.showMaximized()
        return app.exec()
    except Exception as exc:
        error = traceback.format_exc()
        (ROOT / "startup-error.log").write_text(error, encoding="utf-8")
        QMessageBox.critical(None, "启动失败", f"{exc}\n详细信息见 startup-error.log")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
