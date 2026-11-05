"""Pico 自定义 HID 通讯 Demo —— 命令行上位机（纯 Python）。

    python hid_demo.py list                  # 列出系统 HID 设备 / 找 Pico
    python hid_demo.py ping                  # 握手
    python hid_demo.py echo "hello pico"    # 回显 + CRC8 校验
    python hid_demo.py info                 # 固件信息 / uptime
    python hid_demo.py led on|off|read      # 板载 LED(GP25)
    python hid_demo.py adc 0..3             # GP26..GP29 电压(mV)
    python hid_demo.py temp                 # 芯片温度
    python hid_demo.py uptime / stats
    python hid_demo.py stream 100 3         # 每 100ms 主动上报，收 3 秒
    python hid_demo.py bench 200            # 回环压测（RTT / 吞吐）
    python hid_demo.py repl                 # 交互模式
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pico_hid import (  # noqa: E402
    PicoHID, PicoError, list_devices, find_device,
    DEFAULT_VID, DEFAULT_PID, WRITE_MODES, VENDOR_USAGE_PAGE, VENDOR_USAGE,
)


def run(dev: PicoHID, argv) -> str:
    if not argv:
        return ""
    cmd, *rest = argv
    cmd = cmd.lower()

    if cmd == "ping":
        return "PONG  " + dev.ping()

    if cmd == "echo":
        data = " ".join(rest).encode("utf-8")
        got, ok = dev.echo(data)
        return "ECHO  %d B  校验=%s  %r" % (len(got), "OK" if ok else "不一致", got)

    if cmd == "info":
        return "INFO  " + dev.info()

    if cmd == "led":
        op = (rest[0] if rest else "read").lower()
        if op == "on":
            return "LED -> on, 读回 %d" % dev.led_write(True)
        if op == "off":
            return "LED -> off, 读回 %d" % dev.led_write(False)
        if op in ("read", "r"):
            return "LED state = %d" % dev.led_read()
        return "用法: led on|off|read"

    if cmd == "adc":
        ch = int(rest[0]) if rest else 0
        return "ADC ch%d (GP%d) = %d mV" % (ch, 26 + ch, dev.adc(ch))

    if cmd == "temp":
        return "RP2040 芯片温度 = %.2f °C" % dev.temp()

    if cmd == "uptime":
        return "uptime = %d ms" % dev.uptime()

    if cmd == "stats":
        s = dev.stats()
        return "下位机计数  rx=%s tx=%s err=%s" % (s.get("rx"), s.get("tx"), s.get("err"))

    if cmd == "stream":
        period = int(rest[0]) if rest else 100
        seconds = float(rest[1]) if len(rest) > 1 else 3.0
        actual = dev.stream_start(period)
        events = dev.read_events(seconds)
        dev.stream_stop()
        if not events:
            return "STREAM 周期 %d ms，%.1f 秒内未收到 EVT_STATUS 帧" % (actual, seconds)
        lost = sum(1 for i in range(1, len(events)) if events[i][1] != events[i - 1][1] + 1)
        span = events[-1][0] - events[0][0]
        rate = len(events) * 64 / (span / 1000.0) if span > 0 else 0
        return ("STREAM 周期=%d ms 收到=%d 帧 丢/乱序=%d 时间跨度=%d ms "
                "≈ %.0f B/s 末帧: adc0=%d mV led=%d" % (
                    actual, len(events), lost, span, rate,
                    events[-1][2], events[-1][3]))

    if cmd == "bench":
        n = int(rest[0]) if rest else 100
        ok, fail, dt, avg, goodput = dev.bench(n)
        return ("BENCH  %d 次 ECHO  成功=%d 失败=%d  总耗时=%.3f s  "
                "平均RTT=%.2f ms  有效载荷≈%.0f B/s" % (n, ok, fail, dt, avg, goodput))

    if cmd in ("help", "?"):
        return ("命令: ping / echo <text> / info / led on|off|read / adc <0..3> / temp / "
                "uptime / stats / stream <ms> <秒> / bench <n> / quit")

    return "未知命令：%s（输入 help）" % cmd


def do_repl(dev):
    print("交互模式，输入 help 查看命令，quit 退出。")
    while True:
        try:
            line = input("hid> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        try:
            print(run(dev, line.split()))
        except Exception as e:
            print("ERR:", e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raspberry Pi Pico 自定义 HID 通讯 Demo 上位机")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=DEFAULT_VID, help="0 = 不限")
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=DEFAULT_PID, help="0 = 不限")
    ap.add_argument("--path", default=None, help="直接指定 hidapi 设备路径")
    ap.add_argument("--mode", choices=[m[0] for m in WRITE_MODES], default=None,
                    help="跳过握手，强制指定写入格式（id0=首字节补0 / raw=裸64B）")
    ap.add_argument("command", nargs="*", help="命令及参数，留空进入交互模式")
    a = ap.parse_args(argv)

    if a.command and a.command[0].lower() == "list":
        print("系统 HID 设备：")
        list_devices()
        print("\n目标：VID=%04X PID=%04X usage_page=0x%04X usage=0x%02X" % (
            a.vid, a.pid, VENDOR_USAGE_PAGE, VENDOR_USAGE))
        d = find_device(None if a.vid == 0 else a.vid, None if a.pid == 0 else a.pid)
        print("匹配：", ("找到 -> %s / %s" % (d.get("manufacturer_string"),
                                            d.get("product_string"))) if d else "未找到")
        return 0

    try:
        dev = PicoHID(vid=None if a.vid == 0 else a.vid,
                      pid=None if a.pid == 0 else a.pid,
                      path=a.path.encode() if a.path else None,
                      mode=a.mode)
    except (PicoError, OSError) as e:
        print("连接失败：", e)
        return 1

    with dev:
        print("已连接 Pico HID，写入格式 = %s" % dev.mode)
        try:
            if not a.command or a.command[0].lower() == "repl":
                do_repl(dev)
            else:
                print(run(dev, a.command))
        except (TimeoutError, PicoError, OSError, ValueError) as e:
            print("ERR:", e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
