# Rabbit Hole

一款在受限环境（无网络）中通过屏幕传输文件的工具。将文件按固定大小分块后编码为二维码视频流，循环播放，接收端实时解码并检测丢帧，支持重传与剪贴板互操作。

## 特性

- 基于 PyQt5 的跨平台图形界面
- 按固定大小分块并编码为二维码，10 FPS 循环发送
- 实时录屏捕获与解码
- 丢帧检测与缺失帧重传
- 支持从剪贴板读取文本并发送
- 接收端自动更新系统剪贴板

## 环境要求

```bash
# Python 3.7+
pip install -r requirements.txt
```

## 快速开始

### 发送端（sender.py）

```bash
python sender.py [文件路径]
```

1. 点击“加载文件”或“读取剪贴板”
2. 点击“开始发送”
3. 在接收端复制缺失帧编号后，粘贴或手动输入到发送端并点击“重发缺失”

### 接收端（receiver.py）

```bash
python receiver.py
```

1. 点击“启动捕获”开始解码
2. 解码完成后自动保存文件或更新系统剪贴板
3. 点击“复制缺失帧”可复制缺失帧编号至剪贴板
