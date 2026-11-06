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
CMD_SYS_INFO = 0x31        # 主机 -> 设备：PC 系统占用，驱动 OLED 显示
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


# ------------------------------------------------------------- SYS_INFO
# CMD_SYS_INFO 载荷（固定 24 字节，上下位机共用这一份定义）：
#   0      schema 版本 = 1
#   1      CPU 占用   0..100
#   2      内存占用   0..100
#   3      系统盘占用 0..100
#   4      GPU 占用   0..100，0xFF = 不可用
#   5..7   hour / minute / second（主机本地时间）
#   8..23  主机名 ASCII，不足补 0（最多 16 字节）
SYS_INFO_SCHEMA = 1
SYS_INFO_LEN = 24
GPU_UNKNOWN = 0xFF


def _clamp(v, hi):
    v = int(v)
    if v < 0:
        return 0
    if v > hi:
        return hi
    return v


def pack_sysinfo(cpu, mem, disk, gpu=None, hour=0, minute=0, second=0, host=""):
    """把 PC 系统占用打包成 24 字节；gpu 传 None 表示无 GPU 数据。"""
    b = bytearray(SYS_INFO_LEN)
    b[0] = SYS_INFO_SCHEMA
    b[1] = _clamp(cpu, 100)
    b[2] = _clamp(mem, 100)
    b[3] = _clamp(disk, 100)
    b[4] = GPU_UNKNOWN if gpu is None else _clamp(gpu, 100)
    b[5] = _clamp(hour, 23)
    b[6] = _clamp(minute, 59)
    b[7] = _clamp(second, 59)
    n = 0
    for ch in host:
        o = ord(ch)
        if 32 <= o <= 126:          # 只留可打印 ASCII，避免固件字库缺字
            b[8 + n] = o
            n += 1
            if n >= 16:
                break
    return bytes(b)


def unpack_sysinfo(payload):
    """解出 dict，版本/长度不符返回 None。"""
    payload = bytes(payload)
    if len(payload) < SYS_INFO_LEN or payload[0] != SYS_INFO_SCHEMA:
        return None
    gpu = payload[4]
    host = ""
    for c in payload[8:24]:
        if c == 0:
            break
        if 32 <= c <= 126:
            host += chr(c)
    return {
        "cpu": payload[1],
        "mem": payload[2],
        "disk": payload[3],
        "gpu": None if gpu == GPU_UNKNOWN else gpu,
        "time": "%02d:%02d:%02d" % (payload[5], payload[6], payload[7]),
        "host": host,
    }
