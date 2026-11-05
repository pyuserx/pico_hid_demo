"""Pico 自定义 HID 通讯 Demo —— tkinter 图形上位机（纯 Python，单线程非阻塞）。

    python hid_gui.py

不用后台线程：hidapi 设成非阻塞，after(20ms) 轮询收包，界面不会卡。
按键统一右对齐、等宽，日志区可滚动。
"""
from __future__ import annotations

import os
import sys
import time
import struct
import tkinter as tk
from tkinter import ttk, scrolledtext

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "firmware"))
from pico_hid import (  # noqa: E402
    PicoHID, PicoError, find_device, DEFAULT_VID, DEFAULT_PID,
    VENDOR_USAGE_PAGE, VENDOR_USAGE, CMD_NAMES,
)
import hidproto as P  # noqa: E402

BG = "#f4f6f8"
FG = "#1f2328"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pico 自定义 HID 通讯 Demo")
        self.geometry("860x600")
        self.minsize(760, 520)
        self.configure(bg=BG)
        self.dev: PicoHID | None = None
        self.pending = {}
        self.events = 0
        self.bench_left = 0
        self.bench_t0 = 0.0
        self.bench_n = 0
        self._build()
        self.after(20, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="设备：").pack(side="left")
        self.dev_var = tk.StringVar()
        self.dev_cb = ttk.Combobox(top, textvariable=self.dev_var, width=42, state="readonly")
        self.dev_cb.pack(side="left", padx=4)
        ttk.Button(top, text="刷新", width=7, command=self._refresh).pack(side="left", padx=3)
        self.conn_btn = ttk.Button(top, text="连接", width=8, command=self._toggle)
        self.conn_btn.pack(side="left", padx=3)

        body = ttk.Frame(self, padding=(10, 4))
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text=" 命令 ", padding=10)
        left.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(left, text="参数 1").grid(row=0, column=0, sticky="e", pady=2)
        self.arg1 = ttk.Entry(left, width=18)
        self.arg1.insert(0, "hello pico")
        self.arg1.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(left, text="参数 2").grid(row=1, column=0, sticky="e", pady=2)
        self.arg2 = ttk.Entry(left, width=18)
        self.arg2.insert(0, "100")
        self.arg2.grid(row=1, column=1, sticky="ew", pady=2)

        rows = [
            ("PING  握手", lambda: self._send(P.CMD_PING, b"", "PING", "ping")),
            ("ECHO  回显", lambda: self._send(P.CMD_ECHO, self.arg1.get().encode()[:P.MAX_PAYLOAD], "ECHO", "echo")),
            ("INFO  固件信息", lambda: self._send(P.CMD_GET_INFO, b"", "INFO", "info")),
            ("LED  点亮", lambda: self._send(P.CMD_LED_WRITE, b"\x01", "LED_ON", "led")),
            ("LED  熄灭", lambda: self._send(P.CMD_LED_WRITE, b"\x00", "LED_OFF", "led")),
            ("LED  读取", lambda: self._send(P.CMD_LED_READ, b"", "LED_READ", "led")),
            ("ADC  采样", lambda: self._send(P.CMD_ADC_READ, bytes((self._int(self.arg1.get(), 0),)), "ADC", "adc")),
            ("TEMP  温度", lambda: self._send(P.CMD_TEMP_READ, b"", "TEMP", "temp")),
            ("UPTIME  运行时间", lambda: self._send(P.CMD_UPTIME, b"", "UPTIME", "uptime")),
            ("STATS  计数", lambda: self._send(P.CMD_GET_STATS, b"", "STATS", "stats")),
            ("STREAM  开始", self._stream_start),
            ("STREAM  停止", lambda: self._send(P.CMD_STREAM_STOP, b"", "STREAM_OFF", "stream_off")),
            ("BENCH  压测", self._bench),
            ("清空日志", self._clear),
        ]
        for i, (text, fn) in enumerate(rows, start=2):
            ttk.Button(left, text=text, command=fn).grid(
                row=i, column=0, columnspan=2, sticky="ew", pady=2)

        right = ttk.LabelFrame(body, text=" 通讯日志 ", padding=6)
        right.pack(side="left", fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(
            right, wrap="word", font=("Consolas", 10), bg="#ffffff", fg=FG,
            relief="flat", height=22)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e7e34")
        self.log.tag_config("dim", foreground="#6a737d")
        self.log.tag_config("evt", foreground="#2b6cb0")

        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x")
        self.status = tk.StringVar(value="未连接 — 点「刷新」后再点「连接」")
        ttk.Label(bar, textvariable=self.status).pack(side="left")

        self._refresh()
        self._say("参数1：ECHO 文本 / ADC 通道(0-3) / BENCH 次数；参数2：STREAM 周期(ms)。", "dim")
        self._say("协议：64B HID 报告 · 0xA5 帧头 · CRC8 · 无 Report ID", "dim")

    @staticmethod
    def _int(s, default):
        try:
            return int(s)
        except ValueError:
            return default

    def _say(self, text, tag=None):
        self.log.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), text), tag)
        self.log.see("end")

    def _clear(self):
        self.log.delete("1.0", "end")

    # ------------------------------------------------------------ 设备连接
    def _refresh(self):
        import hid
        self._devs = [d for d in hid.enumerate()
                      if d.get("usage_page") == VENDOR_USAGE_PAGE
                      and d.get("usage") == VENDOR_USAGE]
        labels = ["%04X:%04X  %s" % (d["vendor_id"], d["product_id"],
                                    d.get("product_string") or "HID") for d in self._devs]
        self.dev_cb["values"] = labels
        if labels:
            self.dev_cb.current(0)
        self._say("刷新：找到 %d 个自定义 HID 设备" % len(labels), "dim")

    def _toggle(self):
        if self.dev is not None:
            self.dev.close()
            self.dev = None
            self.conn_btn["text"] = "连接"
            self.status.set("已断开")
            self._say("已断开", "dim")
            return
        idx = self.dev_cb.current()
        if idx < 0 or idx >= len(self._devs):
            self._say("没有可连接的设备（Pico 是否已上电并枚举？）", "err")
            return
        try:
            self.dev = PicoHID(info=self._devs[idx])
            self.dev.set_nonblocking(True)
            self.conn_btn["text"] = "断开"
            self.status.set("已连接 · %s · 写入格式 %s" % (self.dev_cb.get(), self.dev.mode))
            self._say("已连接：%s（写入格式 %s）" % (self.dev_cb.get(), self.dev.mode), "ok")
        except (PicoError, OSError) as e:
            self._say("连接失败：%s" % e, "err")

    # -------------------------------------------------------------- 发命令
    def _send(self, cmd, data, label, kind):
        if self.dev is None:
            self._say("请先连接设备", "err")
            return
        try:
            self.dev.seq = (self.dev.seq + 1) & 0xFF
            seq = self.dev.seq
            self.dev._write_report(P.pack(cmd, seq, data))
            self.pending[seq] = (label, kind, time.monotonic())
            self._say("→ %-10s %s" % (label, data.hex(" ") if data else ""), "dim")
        except (PicoError, OSError) as e:
            self._say("发送失败：%s" % e, "err")

    def _stream_start(self):
        ms = max(10, min(65535, self._int(self.arg2.get(), 100)))
        self.events = 0
        self._send(P.CMD_STREAM_START, bytes((ms & 0xFF, ms >> 8)), "STREAM_ON", "stream_on")

    def _bench(self):
        self.bench_n = max(1, min(2000, self._int(self.arg1.get(), 100)))
        self.bench_left = self.bench_n
        self.bench_t0 = time.monotonic()
        self._say("BENCH 开始：%d 次 ECHO 回环" % self.bench_n, "dim")
        self._bench_next()

    def _bench_next(self):
        if self.bench_left <= 0:
            dt = time.monotonic() - self.bench_t0
            self._say("BENCH 完成：%d 次 / %.3f s / 平均 RTT %.2f ms" % (
                self.bench_n, dt, dt / self.bench_n * 1000), "ok")
            return
        self.bench_left -= 1
        self._send(P.CMD_ECHO, b"BENCH", "BENCH", "bench")

    # -------------------------------------------------------------- 收响应
    def _poll(self):
        if self.dev is not None:
            try:
                for _ in range(32):
                    raw = self.dev.read_raw(timeout_ms=None)
                    if not raw:
                        break
                    self.dev.rx_count += 1
                    frame = self.dev._normalize_in(raw)
                    if len(frame) != 64:
                        continue
                    try:
                        self._on_frame(*P.unpack(frame))
                    except ValueError as e:
                        self._say("坏帧：%s" % e, "err")
            except (PicoError, OSError) as e:
                self._say("读取失败：%s" % e, "err")
                self._toggle()
        self.after(20, self._poll)

    def _on_frame(self, cmd, seq, data):
        if cmd == P.EVT_STATUS and len(data) >= 9:
            self.events += 1
            up, cnt, mv, led = struct.unpack("<IHHB", data[:9])
            if self.events % 10 == 0 or self.events == 1:
                self._say("← EVENT   #%-5d up=%-9d ms  adc0=%-5d mV  led=%d" % (
                    cnt, up, mv, led), "evt")
            return

        if cmd in (0xC0, 0xC1):
            self._say("← ERR     %r" % data, "err")
            return

        item = self.pending.pop(seq, None)
        label, kind, t0 = item if item else ("RX", "raw", 0)
        rtt = (time.monotonic() - t0) * 1000 if t0 else 0
        self._say("← %-10s %-45s  RTT %.1f ms" % (label, self._format(kind, data), rtt), "ok")
        if kind == "bench":
            self.after(0, self._bench_next)
        self.status.set("已连接 · RX=%d TX=%d 帧" % (self.dev.rx_count, self.dev.tx_count))

    @staticmethod
    def _format(kind, d):
        try:
            if kind in ("ping", "info"):
                return d.decode("utf-8", "replace").rstrip("\x00")
            if kind == "echo":
                return repr(d.rstrip(b"\x00"))
            if kind == "led":
                return "state=%d" % d[0]
            if kind == "adc" and len(d) >= 2:
                return "%d mV" % struct.unpack("<H", d[:2])[0]
            if kind == "temp" and len(d) >= 2:
                return "%.2f °C" % (struct.unpack("<h", d[:2])[0] / 100.0)
            if kind == "uptime" and len(d) >= 4:
                return "%d ms" % struct.unpack("<I", d[:4])[0]
            if kind == "stats" and len(d) >= 3:
                return "rx=%d tx=%d err=%d" % (d[0], d[1], d[2])
            if kind == "stream_on" and len(d) >= 2:
                return "周期 %d ms" % struct.unpack("<H", d[:2])[0]
        except Exception:
            pass
        return d.hex(" ")


if __name__ == "__main__":
    App().mainloop()
