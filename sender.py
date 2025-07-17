# 发送端程序，实现文件加载、分块编码为二维码视频流、循环播放及缺失帧重传功能

import sys
import os
import argparse
import cv2
import base64

try:
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")  # Fix opencv bugs
except Exception as e:
    pass

import numpy as np
import qrcode
from PyQt5 import QtWidgets, QtCore, QtGui

CHUNK_SIZE = 1600  # 每个数据块大小，单位字节
FPS = 10  # 视频帧率


class SenderWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rabbit Hole 发送端")
        self.resize(400, 300)

        self.file_data = b""
        self.chunks = []
        self.missing_frames = set()
        self.current_frame_index = 0
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.next_frame)

        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        self.load_btn = QtWidgets.QPushButton("加载文件")
        self.load_btn.clicked.connect(self.load_file)
        layout.addWidget(self.load_btn)

        # 新增：读取剪贴板按钮
        self.clip_btn = QtWidgets.QPushButton("读取剪贴板")
        self.clip_btn.clicked.connect(self.load_clipboard)
        layout.addWidget(self.clip_btn)

        self.start_btn = QtWidgets.QPushButton("开始发送")
        self.start_btn.clicked.connect(self.start_sending)
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        self.missing_input = QtWidgets.QLineEdit()
        self.missing_input.setPlaceholderText("输入缺失帧编号，用逗号分隔")
        layout.addWidget(self.missing_input)

        self.resend_btn = QtWidgets.QPushButton("重发缺失")
        self.resend_btn.clicked.connect(self.resend_missing)
        self.resend_btn.setEnabled(False)
        layout.addWidget(self.resend_btn)

        self.video_label = QtWidgets.QLabel()
        self.video_label.setFixedSize(360, 360)
        layout.addWidget(self.video_label, alignment=QtCore.Qt.AlignHCenter)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setEnabled(False)
        layout.addWidget(self.progress)
        # 显示一轮发送用时
        self.time_label = QtWidgets.QLabel("一轮时间: N/A")
        layout.addWidget(self.time_label)

        self.setLayout(layout)

    def load_file(self, path=None):
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件")
        if path != "":
            with open(path, "rb") as f:
                self.file_data = f.read()
            self.filename = os.path.basename(path)
            self.chunks = [self.file_data[i : i + CHUNK_SIZE] for i in range(0, len(self.file_data), CHUNK_SIZE)]
            self.send_ids = list(range(len(self.chunks)))
            self.current_frame_index = 0
            self.missing_frames.clear()
            self.start_btn.setEnabled(True)
            self.resend_btn.setEnabled(True)
            self.progress.setRange(0, len(self.chunks) - 1)
            self.progress.setValue(0)
            # QtWidgets.QMessageBox.information(self, "提示", f"文件加载成功，共 {len(self.chunks)} 帧")
            self.time_label.setText(f"共 {len(self.chunks)} 帧，一轮时间预计: {len(self.chunks) / FPS:.2f}秒")

    def start_sending(self):
        self.timer.start(1000 // FPS)
        self.start_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.clip_btn.setEnabled(False)
        self.progress.setEnabled(True)

    def next_frame(self):
        if not self.chunks:
            return
        data = self.chunks[self.send_ids[self.current_frame_index]]
        qr_img = self.make_qr(data, self.send_ids[self.current_frame_index])
        self.show_frame(qr_img)
        self.progress.setValue(self.current_frame_index)
        self.current_frame_index = (self.current_frame_index + 1) % len(self.send_ids)

    def make_qr(self, data, index):
        # 先拼接原始二进制数据，再做 Base64 编码
        raw = f"{self.filename}|{index:06d}|{len(self.chunks):06d}|".encode() + data
        b64_payload = base64.b64encode(raw)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(b64_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img = img.resize((360, 360))
        # 转为OpenCV格式
        img_cv = np.array(img)
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)
        return img_cv

    def show_frame(self, frame):
        # 显示在界面上
        img = QtGui.QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QtGui.QImage.Format_BGR888)
        self.video_label.setPixmap(QtGui.QPixmap.fromImage(img))

    def resend_missing(self):
        text = self.missing_input.text()
        if text == "":
            self.send_ids = list(range(len(self.chunks)))
            self.time_label.setText(f"共 {len(self.chunks)} 帧，一轮时间预计: {len(self.chunks) / FPS:.2f}秒")
        else:
            if not text.strip():
                return
            try:
                self.send_ids = [int(x.strip()) for x in text.split(",") if x.strip().isdigit()]
            except Exception:
                QtWidgets.QMessageBox.warning(self, "错误", "请输入正确的缺失帧编号，逗号分隔")
                return
            self.time_label.setText(
                f"共 {len(self.send_ids)}/{len(self.chunks)} 帧，一轮时间预计: {len(self.send_ids) / FPS:.2f}秒"
            )
        self.progress.setRange(0, len(self.send_ids) - 1)
        self.current_frame_index = 0


    def load_clipboard(self):
        # 从剪贴板读取文本并当作二进制数据发送
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            QtWidgets.QMessageBox.warning(self, "错误", "剪切板为空")
            return
        data = text.encode()
        self.file_data = data
        self.filename = "clipboard.txt"
        self.chunks = [self.file_data[i : i + CHUNK_SIZE] for i in range(0, len(self.file_data), CHUNK_SIZE)]
        self.send_ids = list(range(len(self.chunks)))
        self.current_frame_index = 0
        self.missing_frames.clear()
        self.start_btn.setEnabled(True)
        self.resend_btn.setEnabled(True)
        self.progress.setRange(0, len(self.chunks) - 1)
        self.progress.setValue(0)
        self.time_label.setText(f"共 {len(self.chunks)} 帧，一轮时间预计: {len(self.chunks) / FPS:.2f}秒")

def main():
    parser = argparse.ArgumentParser(description="Rabbit Hole 发送端")
    parser.add_argument("file", nargs="?", help="要发送的文件路径")
    args = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    win = SenderWindow()
    win.show()
    if args.file:
        if os.path.isfile(args.file):
            win.load_file(args.file)
            win.start_sending()
        else:
            print(f"文件不存在: {args.file}", file=sys.stderr)
            sys.exit(1)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
