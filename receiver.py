# 接收端程序，实现录屏、二维码实时解码、丢帧检测及缺失帧列表显示

import sys
import cv2
import numpy as np
from pyzbar import pyzbar
import os

try:
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")  # Fix opencv bugs
except Exception as e:
    pass
import contextlib
import base64
from PyQt5 import QtWidgets, QtCore, QtGui

import mss
import mss.windows

mss.windows.CAPTUREBLT = 0  # Fix mouse flickering


class ProgressCanvas(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.received = set()
        self.total = 0
        self.cols = 50  # 每行格子数，可按需调整

    def setData(self, received: set, total: int):
        self.received = received
        self.total = total
        self.update()

    def paintEvent(self, event):
        if self.total <= 0:
            return
        painter = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        cols = self.cols
        rows = (self.total + cols - 1) // cols
        cell_w = w / cols
        cell_h = h / rows
        for i in range(self.total):
            r = i // cols
            c = i % cols
            rect = QtCore.QRectF(c * cell_w, r * cell_h, cell_w, cell_h)
            color = QtGui.QColor(0, 180, 0) if i in self.received else QtGui.QColor(180, 0, 0)
            painter.fillRect(rect, color)
        painter.end()


class ReceiverWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rabbit Hole 接收端")
        self.resize(400, 300)

        self.captured_frames = {}
        self.total_frames = None
        self.missing_frames = set()
        self.output_filename = None

        self.capture = None
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.grab_frame)

        # 新：mss 截屏实例
        self.sct = mss.mss()

        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()
        # 优化整体边距和间距
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 按钮横向布局：启动、停止、复制缺失
        self.start_btn = QtWidgets.QPushButton("启动捕获")
        self.start_btn.clicked.connect(self.start_capture)
        self.stop_btn = QtWidgets.QPushButton("停止捕获")
        self.stop_btn.clicked.connect(self.stop_capture)
        self.stop_btn.setEnabled(False)
        self.copy_btn = QtWidgets.QPushButton("复制缺失帧")
        self.copy_btn.clicked.connect(self.copy_missing)
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.copy_btn)
        layout.addLayout(btn_layout)

        # 进度文字
        self.count_label = QtWidgets.QLabel("已传输0/0个")
        self.count_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.count_label)

        # 进度画板（高度缩小）

        layout.addStretch(1)
        self.canvas = ProgressCanvas(self)
        self.canvas.setFixedHeight(200)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self.canvas)

        self.setLayout(layout)

    def start_capture(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.captured_frames.clear()
        self.missing_frames.clear()
        self.total_frames = None
        self.output_filename = None
        self.timer.start(50)

    def stop_capture(self):
        self.timer.stop()
        if self.capture:
            self.capture.release()
            self.capture = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if len(self.captured_frames.keys()) == self.total_frames:
            data_bytes = b"".join(self.captured_frames[i] for i in sorted(self.captured_frames))
            if self.output_filename == "clipboard.txt":
                try:
                    text = data_bytes.decode()
                except Exception:
                    text = ""
                QtWidgets.QApplication.clipboard().setText(text)
                QtWidgets.QMessageBox.information(self, "提示", "剪切板内容已更新")
            else:
                with open(self.output_filename, "wb") as f:
                    f.write(data_bytes)
                QtWidgets.QMessageBox.information(self, "提示", f"文件已保存: {self.output_filename}")

    def grab_frame(self):
        # 从屏幕获取截图（mss）
        sct_img = self.sct.grab(self.sct.monitors[1])  # monitors[1] 是主显示器
        arr = np.array(sct_img)  # 得到的是 BGRA 格式数据
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

        self.decode_frame(frame)
        self.update_missing()

    def decode_frame(self, frame):
        with open(os.devnull, "w") as f, contextlib.redirect_stderr(f):
            decoded_objs = pyzbar.decode(frame)
        for obj in decoded_objs:
            # 先 base64 解码，再按 '|' 拆分
            try:
                raw = base64.b64decode(obj.data)
            except Exception:
                continue
            parts = raw.split(b"|", 3)
            if len(parts) != 4:  # 非数据流二维码
                continue
            filename_bytes, index_bytes, total_bytes, chunk_data = parts
            # total_frames 从解码部分解析
            try:
                filename = filename_bytes.decode()
                index = int(index_bytes)
                if self.total_frames is None:
                    self.total_frames = int(total_bytes)
                else:
                    assert self.total_frames == int(total_bytes)

                if self.output_filename is None:
                    self.output_filename = filename
                else:
                    assert self.output_filename == filename

                assert index >= 0 and index < self.total_frames
                if index not in self.captured_frames:
                    self.captured_frames[index] = chunk_data
            except Exception:
                continue

    def update_missing(self):
        if self.total_frames is None:
            return
        expected = set(range(self.total_frames))
        received = set(self.captured_frames.keys())
        missing = expected - received
        self.missing_frames = missing
        # 更新已传输计数 & 画板
        self.count_label.setText(f"已传输{len(received)}/{self.total_frames}个")
        self.canvas.setData(received, self.total_frames)

        # 获取完成时自动停止
        if len(self.captured_frames.keys()) == self.total_frames:
            self.stop_capture()

    def copy_missing(self):
        # 复制缺失帧到剪贴板
        text = ", ".join(str(i) for i in sorted(self.missing_frames))
        QtWidgets.QApplication.clipboard().setText(text)
        QtWidgets.QMessageBox.information(self, "提示", "已复制缺失帧到剪贴板")


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = ReceiverWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
