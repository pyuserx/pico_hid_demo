"""上位机侧：Pico 自定义 HID 通讯（对齐 firmware/hidproto.py）。

协议定义直接复用固件目录里的 hidproto.py —— 只有一份 CRC8/命令表/帧结构，
上下位机改协议时不会出现两边不同步。

依赖：pip install hidapi  （import 名为 hid）
"""
from __future__ import annotations

import os
import sys
import time
import struct
import hid

_FIRMWARE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware")
if _FIRMWARE not in sys.path:
    sys.path.insert(0, _FIRMWARE)
import hidproto as P  # noqa: E402  —— 与下位机共用同一份协议定义

REPORT_LEN = 64
DEFAULT_VID = 0x2E8A
DEFAULT_PID = 0x0005
VENDOR_USAGE_PAGE = 0xFF00
VENDOR_USAGE = 0x01

# 下位机命令（来自 hidproto.py），这里只做语义封装
CMD = {name: getattr(P, name) for name in (
    "CMD_PING", "CMD_ECHO", "CMD_GET_INFO", "CMD_LED_WRITE", "CMD_LED_READ",
    "CMD_ADC_READ", "CMD_TEMP_READ", "CMD_UPTIME", "CMD_STREAM_START",
    "CMD_STREAM_STOP", "CMD_GET_STATS", "EVT_STATUS", "ERR_BAD_CMD", "ERR_BAD_FRAME",
)}
ERR_CODES = {0xC0: "未知命令", 0xC1: "非法帧 / 参数"}

CMD_NAMES = {
    P.CMD_PING: "PING", P.CMD_ECHO: "ECHO", P.CMD_GET_INFO: "INFO",
    P.CMD_LED_WRITE: "LED_W", P.CMD_LED_READ: "LED_R", P.CMD_ADC_READ: "ADC",
    P.CMD_TEMP_READ: "TEMP", P.CMD_UPTIME: "UPTIME",
    P.CMD_STREAM_START: "STREAM_ON", P.CMD_STREAM_STOP: "STREAM_OFF",
    P.CMD_GET_STATS: "STATS", P.EVT_STATUS: "EVENT",
}


class PicoError(Exception):
    pass


def list_devices(verbose=True):
    """列出系统里的 HID 设备，便于排查设备发现失败。"""
    items = hid.enumerate()
    if verbose:
        for d in items:
            print("VID=%04X PID=%04X up=0x%04X use=0x%02X if=%-3s %s / %s" % (
                d["vendor_id"], d["product_id"], d.get("usage_page", 0),
                d.get("usage", 0), d.get("interface_number", -1),
                d.get("manufacturer_string") or "", d.get("product_string") or ""))
    return items


def find_device(vid=DEFAULT_VID, pid=DEFAULT_PID, path=None):
    """定位 Pico：严格匹配 usage_page=0xFF00 / usage=0x01，避免误连别的厂商 HID。"""
    items = hid.enumerate()
    if path is not None:
        for d in items:
            if d["path"] == path:
                return d
        raise PicoError("指定的 HID path 不存在")

    def ok(d, strict):
        if d.get("usage_page") != VENDOR_USAGE_PAGE or d.get("usage") != VENDOR_USAGE:
            return False
        if strict:
            return (vid is None or d["vendor_id"] == vid) and (pid is None or d["product_id"] == pid)
        return True

    for strict in (True, False):
        hit = [d for d in items if ok(d, strict)]
        if hit:
            return hit[0]
    return None


# hidapi 在不同平台/版本上，对「无 Report ID 的 64 字节报告」的写入格式略有差异：
#   - 标准做法：首字节补 0x00 作为 Report ID，共 65 字节（Windows / hidapi 通用规则）
#   - 少数 Linux hidraw 组合：直接发 64 字节
# 握手时用新句柄逐个试探，只看 hid_write 是否成功，避免失败的 overlapped I/O
# 残留把句柄卡死。
WRITE_MODES = [
    ("id0", lambda frame: b"\x00" + frame),
    ("raw", lambda frame: bytes(frame)),
]


class PicoHID:
    def __init__(self, info=None, vid=DEFAULT_VID, pid=DEFAULT_PID, path=None,
                 mode=None, probe=True):
        self.device_info = info or find_device(vid, pid, path)
        if self.device_info is None:
            raise PicoError(
                "未找到 Pico 自定义 HID（usage_page=0xFF00, usage=0x01）。\n"
                "  1) 每次上电后先运行 python tools/activate.py 激活 HID；\n"
                "  2) 必要时拔插 USB 重新上电（复位后不会自动重新枚举）；\n"
                "  3) 用 `python host/hid_demo.py list` 看现有 HID 设备。")
        self.h = None
        self.mode = None
        self.seq = 0
        self.backlog = []
        self.rx_count = 0
        self.tx_count = 0
        self._open()
        if mode:
            self.mode = mode
        elif probe:
            self._probe_write_mode()
        else:
            self.mode = "id0"

    # ---------------------------------------------------------------- 底层
    def _open(self):
        self._close()
        self.h = hid.device()
        try:
            self.h.open_path(self.device_info["path"])
        except OSError as e:
            raise PicoError("打开 HID 设备失败（被系统独占或已断开）：%s" % e)
        self.h.set_nonblocking(0)

    def _close(self):
        if self.h is not None:
            try:
                self.h.close()
            except Exception:
                pass
            self.h = None

    def close(self):
        self._close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def set_nonblocking(self, value=True):
        self.h.set_nonblocking(1 if value else 0)

    def _write_report(self, frame):
        builder = dict(WRITE_MODES)[self.mode or "id0"]
        n = self.h.write(builder(frame))
        if n <= 0:
            raise PicoError("hid_write 失败 (%s)：%s" % (self.mode, self.h.error()))
        self.tx_count += 1
        return n

    def _probe_write_mode(self):
        """发一个 PING 帧试探写入格式。每次失败都换一个新句柄。"""
        frame = P.pack(P.CMD_PING, 0)
        errors = []
        for name, _ in WRITE_MODES:
            try:
                self._open()
                self.mode = name
                self._write_report(frame)
                self.mode = name
                return name
            except (PicoError, OSError) as e:
                errors.append("%s: %s" % (name, e))
        self._close()
        raise PicoError(
            "HID 输出报告写入失败，已试过所有格式：\n  %s\n"
            "常见原因：设备上 main.py 未运行（OUT 端点没挂传输）、"
            "或 boot.py 的 HID 未配置成功。请重新上电并用 `selftest.py` 排查。" %
            "\n  ".join(errors))

    def _normalize_in(self, raw):
        """把 hidapi 读到的 64 或 65 字节统一成 64 字节裸帧。"""
        b = bytes(raw)
        if not b:
            return b""
        if len(b) > REPORT_LEN and b[0] == 0x00:
            b = b[1:]
        return b[:REPORT_LEN]

    def read_raw(self, timeout_ms=1000):
        try:
            if timeout_ms is None:
                return self.h.read(128)
            return self.h.read(128, timeout_ms)
        except TypeError:                 # 老版本 hidapi
            return self.h.read(128)

    def read_frame(self, timeout_ms=1000):
        """返回 (cmd, seq, payload)，坏帧/超时返回 None。"""
        raw = self.read_raw(timeout_ms)
        if not raw:
            return None
        self.rx_count += 1
        frame = self._normalize_in(raw)
        if len(frame) != REPORT_LEN:
            return None
        try:
            return P.unpack(frame)
        except ValueError:
            return None

    def transact(self, cmd, payload=b"", timeout=1.5):
        self.seq = (self.seq + 1) & 0xFF
        seq = self.seq
        self._write_report(P.pack(cmd, seq, payload))
        want = (cmd | 0x80) & 0xFF
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError("%s 超时 (%.2fs)" % (CMD_NAMES.get(cmd, hex(cmd)), timeout))
            f = self.read_frame(max(1, int(remain * 1000)))
            if f is None:
                continue
            c, s, d = f
            if c in ERR_CODES:
                raise PicoError("设备返回错误 %s：%r" % (ERR_CODES[c], d))
            if c == want and s == seq:
                return d
            self.backlog.append(f)       # 事件帧 / 乱序帧留给调用方

    # ------------------------------------------------------------ 语义命令
    def ping(self):
        return self.transact(P.CMD_PING).decode("utf-8", "replace").rstrip("\x00")

    def info(self):
        return self.transact(P.CMD_GET_INFO).decode("utf-8", "replace").rstrip("\x00")

    def echo(self, data):
        data = bytes(data)[:P.MAX_PAYLOAD]
        got = self.transact(P.CMD_ECHO, data)
        return got, got == data

    def led_write(self, on):
        return self.transact(P.CMD_LED_WRITE, bytes((1 if on else 0,)))[0]

    def led_read(self):
        return self.transact(P.CMD_LED_READ)[0]

    def adc(self, ch=0):
        d = self.transact(P.CMD_ADC_READ, bytes((ch,)))
        return struct.unpack("<H", d[:2])[0] if len(d) >= 2 else 0

    def temp(self):
        d = self.transact(P.CMD_TEMP_READ)
        return struct.unpack("<h", d[:2])[0] / 100.0 if len(d) >= 2 else None

    def uptime(self):
        d = self.transact(P.CMD_UPTIME)
        return struct.unpack("<I", d[:4])[0] if len(d) >= 4 else 0

    def stats(self):
        d = self.transact(P.CMD_GET_STATS)
        return {"rx": d[0], "tx": d[1], "err": d[2]} if len(d) >= 3 else {}

    def stream_start(self, period_ms=100):
        d = self.transact(P.CMD_STREAM_START, bytes((period_ms & 0xFF, period_ms >> 8)))
        return struct.unpack("<H", d[:2])[0] if len(d) >= 2 else period_ms

    def stream_stop(self):
        return self.transact(P.CMD_STREAM_STOP)

    def read_events(self, seconds=3.0):
        """收集 EVT_STATUS 主动上报帧，返回 [(uptime_ms, counter, adc0_mV, led), ...]。"""
        out = []
        deadline = time.monotonic() + seconds
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            f = self.read_frame(max(1, int(remain * 1000)))
            if f is None:
                continue
            c, s, d = f
            if c == P.EVT_STATUS and len(d) >= 9:
                up, cnt, mv = struct.unpack("<IHH", d[:8])
                out.append((up, cnt, mv, d[8]))
            else:
                self.backlog.append(f)
        return out

    def bench(self, n=100, payload=b"BENCH", timeout=1.5):
        """连续 n 次 ECHO 回环，返回 (成功, 失败, 耗时, 平均RTT_ms, 有效速率B/s)。"""
        payload = bytes(payload)[:P.MAX_PAYLOAD]
        ok = fail = 0
        t0 = time.perf_counter()
        for _ in range(n):
            try:
                got, good = self.echo(payload)
                ok += 1 if good else 0
                fail += 0 if good else 1
            except Exception:
                fail += 1
        dt = time.perf_counter() - t0
        avg = dt / max(1, ok + fail) * 1000
        return ok, fail, dt, avg, (ok * len(payload) * 2 / dt if dt else 0)
