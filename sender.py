# 发送端程序，实现文件加载、分块编码为二维码视频流、循环播放及缺失帧重传功能

import sys
import os
import argparse
import base64

try:
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")  # Fix opencv bugs
except Exception as e:
    pass

import time
import numpy as np
import qrcode
import multiprocessing
from PyQt5 import QtWidgets, QtCore, QtGui

CHUNK_SIZE = 2048  # 每个数据块大小，单位字节
FPS = 5  # 视频帧率
IMAGE_SIZE = 400
MAX_WORKERS = 8


def sec2time(s):
    if s == 0:
        return "0秒"
    units = [(31536000, "年"), (86400, "天"), (3600, "小时"), (60, "分"), (1, "秒")]
    r = []
    for t, u in units:
        if s >= t:
            r.append(f"{int(s // t)}{u}")
            s %= t
    return "".join(r)


def generate_qr(filename, index, chunk_size, data):
    # 先拼接原始二进制数据，再做 Base64 编码
    raw = f"{filename}|{index}|{chunk_size}|".encode() + data
    b64_payload = base64.b64encode(raw)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(b64_payload)
    qr.make(fit=True)
    img = qr.make_image().resize((IMAGE_SIZE, IMAGE_SIZE)).convert("RGB")
    return np.array(img)


def qr_worker(
    worker_info,
    filename,
    send_ids,
    chunks,
    output_queue,
):
    worker_id, worker_size = worker_info
    while True:
        total = len(chunks)
        for i, idx in enumerate(send_ids):
            if i % worker_size != worker_id:
                continue

            img = generate_qr(filename, idx, total, chunks[idx])
            output_queue.put(img)
        time.sleep(0.01)


class SenderWindow(QtWidgets.QWidget):
    fileToLoad = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.last_time = time.time()
        self.setWindowTitle("Rabbit Hole 发送端")
        # self.resize(400, 300)

        self.file_data = b""
        self.chunks = []
        self.missing_frames = set()
        self.current_frame_index = 0
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.next_frame)
        self.child_windows = []

        self.init_ui()
        self.fileToLoad.connect(lambda path: self.load_file(path=path))

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        sub_layout = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("加载文件")
        self.load_btn.clicked.connect(self.load_file)
        sub_layout.addWidget(self.load_btn)

        self.clip_btn = QtWidgets.QPushButton("读取剪贴板")
        self.clip_btn.clicked.connect(self.load_clipboard)
        sub_layout.addWidget(self.clip_btn)
        layout.addLayout(sub_layout)

        self.sub_btn = QtWidgets.QPushButton("创建子窗口")
        self.sub_btn.setEnabled(False)
        self.sub_btn.clicked.connect(self.create_subwindow)
        layout.addWidget(self.sub_btn)

        self.start_btn = QtWidgets.QPushButton("开始发送")
        self.start_btn.clicked.connect(self.start_sending)
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        sub_layout = QtWidgets.QHBoxLayout()

        self.missing_input = QtWidgets.QLineEdit()
        self.missing_input.setPlaceholderText("输入缺失帧编号，用逗号分隔")
        self.missing_input.setEnabled(False)
        self.missing_input.returnPressed.connect(self.resend_missing)
        sub_layout.addWidget(self.missing_input)

        self.resend_btn = QtWidgets.QPushButton("重发缺失")
        self.resend_btn.clicked.connect(self.resend_missing)
        self.resend_btn.setEnabled(False)
        sub_layout.addWidget(self.resend_btn)
        layout.addLayout(sub_layout)

        self.video_label = QtWidgets.QLabel()
        self.video_label.setFixedSize(IMAGE_SIZE, IMAGE_SIZE)
        layout.addWidget(self.video_label, alignment=QtCore.Qt.AlignHCenter)

        sub_layout = QtWidgets.QHBoxLayout()

        self.time_label = QtWidgets.QLabel("一轮时间: N/A")
        self.time_label.setHidden(True)
        sub_layout.addWidget(self.time_label)

        self.fps_label = QtWidgets.QLabel("FPS: 0")
        sub_layout.addWidget(self.fps_label, alignment=QtCore.Qt.AlignHCenter)
        layout.addLayout(sub_layout)

        self.setLayout(layout)

    def load_file(self, *, path=None):
        autostart = path is not None
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件")

        if path != "":
            with open(path, "rb") as f:
                self.file_data = f.read()
            self.filename = os.path.basename(path)
            self.pre_start_sending()

        if autostart:
            self.start_sending()

    def load_clipboard(self):
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            QtWidgets.QMessageBox.warning(self, "错误", "剪切板为空")
            return
        data = text.encode()
        self.file_data = data
        self.filename = "clipboard.txt"
        self.pre_start_sending()

    def pre_start_sending(self, *, force_start=False):
        self.chunks = [self.file_data[i : i + CHUNK_SIZE] for i in range(0, len(self.file_data), CHUNK_SIZE)]
        self.send_ids = list(range(len(self.chunks)))
        self.missing_frames.clear()

        self.time_label.setHidden(False)
        self.time_label.setText(f"共 {len(self.chunks)} 帧，一轮时间预计: {sec2time(len(self.chunks) / FPS)}")
        self.sub_btn.setEnabled(True)
        self.start_btn.setEnabled(True)

    def start_qr_producer(self):
        # Terminate pervious workers
        if not hasattr(self, "qr_workers"):
            self.qr_workers = []
        else:
            for w in self.qr_workers:
                w.terminate()
            self.qr_workers = []

        # Flush queue
        if not hasattr(self, "queue"):
            self.queue = multiprocessing.Queue(maxsize=32)
        else:
            while not self.queue.empty():
                self.queue.get()

        # Create new workers
        for i in range(MAX_WORKERS):
            proc = multiprocessing.Process(
                target=qr_worker,
                args=(
                    (i, MAX_WORKERS),
                    self.filename,
                    self.send_ids,
                    self.chunks,
                    self.queue,
                ),
                daemon=True,
            )
            proc.start()

            self.qr_workers.append(proc)

    def next_qr(self):
        return self.queue.get(timeout=1)

    def start_sending(self):
        self.start_qr_producer()
        self.timer.start(1000 // FPS)
        self.start_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.clip_btn.setEnabled(False)
        self.resend_btn.setEnabled(True)
        self.missing_input.setEnabled(True)

    def next_frame(self):
        try:
            qr_img = self.next_qr()
        except Exception:
            return
        self.show_frame(qr_img)

        self.child_windows = [cw for cw in self.child_windows if cw.isVisible()]
        for cw in self.child_windows:
            cw.next_frame()

    def show_frame(self, frame):
        img = QtGui.QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QtGui.QImage.Format_BGR888)
        self.video_label.setPixmap(QtGui.QPixmap.fromImage(img))
        now = time.time()
        fps = 1 / (now - self.last_time) if now != self.last_time else 0
        self.last_time = now
        self.fps_label.setText(f"FPS: {fps:.2f}")

    def resend_missing(self):
        text = self.missing_input.text()
        if text == "":
            self.send_ids = list(range(len(self.chunks)))
            self.time_label.setText(f"共 {len(self.chunks)} 帧，一轮时间预计: {sec2time(len(self.chunks) / FPS)}")
        else:
            if not text.strip():
                return
            try:
                self.send_ids = [int(x.strip()) for x in text.split(",") if x.strip().isdigit()]
            except Exception:
                QtWidgets.QMessageBox.warning(self, "错误", "请输入正确的缺失帧编号，逗号分隔")
                return
            self.time_label.setText(
                f"共 {len(self.send_ids)}/{len(self.chunks)} 帧，一轮时间预计: {sec2time(len(self.send_ids) / FPS)}"
            )
        self.start_qr_producer()  # Restart QR producer

    def create_subwindow(self):
        cw = ChildWindow(self)
        self.child_windows.append(cw)
        cw.show()

    def closeEvent(self, event):
        # Close all workers
        if hasattr(self, "qr_workers"):
            for w in self.qr_workers:
                w.terminate()

        for cw in self.child_windows:
            cw.close()


class ChildWindow(QtWidgets.QWidget):
    def __init__(self, parent_sender):
        super().__init__()
        self.parent_sender = parent_sender
        self.setWindowTitle("🐇")
        self.video_label = QtWidgets.QLabel()
        self.video_label.setFixedSize(IMAGE_SIZE, IMAGE_SIZE)
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.video_label, alignment=QtCore.Qt.AlignHCenter)
        self.fps_label = QtWidgets.QLabel("FPS: 0")
        layout.addWidget(self.fps_label, alignment=QtCore.Qt.AlignHCenter)
        self.setLayout(layout)
        self.last_time = time.time()

    def show_frame(self, frame):
        img = QtGui.QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QtGui.QImage.Format_BGR888)
        self.video_label.setPixmap(QtGui.QPixmap.fromImage(img))
        now = time.time()
        fps = 1 / (now - self.last_time) if now != self.last_time else 0
        self.last_time = now
        self.fps_label.setText(f"FPS: {fps:.2f}")

    def next_frame(self):
        try:
            qr_img = self.parent_sender.next_qr()
        except Exception:
            return
        self.show_frame(qr_img)


def main():
    parser = argparse.ArgumentParser(description="Rabbit Hole 发送端")
    parser.add_argument("file", nargs="?", help="要发送的文件路径")
    args = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    win = SenderWindow()
    win.show()
    if args.file:
        if os.path.isfile(args.file):
            QtCore.QTimer.singleShot(0, lambda path=args.file: win.fileToLoad.emit(path))
        else:
            print(f"文件不存在: {args.file}", file=sys.stderr)
            sys.exit(1)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
