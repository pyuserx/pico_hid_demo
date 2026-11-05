# hidproto.py — 自定义 HID 帧格式（固件侧）
#
# 每帧固定 64 字节（HID 报文长度固定，不足部分补 0）：
#   偏移 0 : 0xA5            帧头
#   偏移 1 : cmd             命令字（响应时最高位置 1）
#   偏移 2 : seq             序号，主机自增，响应原样带回
#   偏移 3 : len             负载长度 0..59
#   偏移 4 : payload...      负载
#   偏移 4+len : crc8        对 [0, 4+len) 做 CRC-8/ATM(0x07)
#   其余填 0
#
# 64 = 4(头) + 59(负载) + 1(CRC)

MAGIC = 0xA5
MAX_PAYLOAD = 59

# 命令字
CMD_PING = 0x01
CMD_ECHO = 0x02
CMD_GET_INFO = 0x03
CMD_LED_WRITE = 0x10
CMD_LED_READ = 0x11
CMD_ADC_READ = 0x12
CMD_TEMP_READ = 0x13
CMD_UPTIME = 0x14
CMD_STREAM_START = 0x20
CMD_STREAM_STOP = 0x21
CMD_GET_STATS = 0x30
EVT_STATUS = 0xE0          # 设备主动上报（不响应）

ERR_BAD_CMD = 0x40
ERR_BAD_FRAME = 0x41


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def pack(cmd, seq, payload=b""):
    payload = bytes(payload)
    n = len(payload)
    if n > MAX_PAYLOAD:
        raise ValueError("payload too long: %d" % n)
    frame = bytearray(64)
    frame[0] = MAGIC
    frame[1] = cmd
    frame[2] = seq & 0xFF
    frame[3] = n
    frame[4:4 + n] = payload
    frame[4 + n] = crc8(frame[0:4 + n])
    return bytes(frame)


def unpack(frame):
    """返回 (cmd, seq, payload)，帧非法时抛出 ValueError。"""
    if len(frame) != 64:
        raise ValueError("frame size %d != 64" % len(frame))
    if frame[0] != MAGIC:
        raise ValueError("bad magic 0x%02X" % frame[0])
    n = frame[3]
    if n > MAX_PAYLOAD:
        raise ValueError("bad length %d" % n)
    if crc8(frame[0:4 + n]) != frame[4 + n]:
        raise ValueError("crc mismatch")
    return frame[1], frame[2], bytes(frame[4:4 + n])
