"""最简指令上位机：只干一件事 —— 确认 Pico 自定义 HID 通讯是否 OK。

    python host/check_link.py

不接串口、不烧录、不改板内任何东西，只通过 HID 报告发几条指令并打印结果。
失败时会给出下位机侧的排查方向。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pico_hid import (  # noqa: E402
    PicoHID, PicoError, find_device, DEFAULT_VID, DEFAULT_PID,
    VENDOR_USAGE_PAGE, VENDOR_USAGE,
)

LINE = "=" * 58


def fail(msg, hint):
    print("\n[FAIL] " + msg)
    print("       排查：" + hint)
    return 1


def main():
    print(LINE)
    print(" Pico 自定义 HID · 链路确认")
    print(LINE)

    d = find_device()
    if d is None:
        return fail(
            "没找到 Pico HID (usage_page=0xFF00 / usage=0x01)",
            "① 已自动尝试通过 CDC 串口激活，失败原因见上方 [auto] 日志；"
            "② 常见原因：Thonny / 串口监视器占用 COM 口，关掉后重试；"
            "③ 也可手动跑 python tools/activate.py；④ 必要时拔插 USB 重新上电")

    print("[1/6] 设备   VID=%04X PID=%04X  iface=%s  %s" % (
        d["vendor_id"], d["product_id"], d.get("interface_number"),
        d.get("product_string") or ""))

    try:
        dev = PicoHID(info=d)
    except (PicoError, OSError) as e:
        return fail("打开/握手失败：%s" % e,
                    "设备上 OUT 端点没挂传输 → main.py 可能没运行；重新上电后试 selftest.py")

    print("[2/6] 报告   64B，写入格式=%s（%s）" % (
        dev.mode, "首字节补 0x00" if dev.mode == "id0" else "裸 64B"))

    with dev:
        try:
            print("[3/6] PING  → %s" % dev.ping())
        except Exception as e:
            return fail("PING 无响应：%s" % e,
                        "下位机没收到/没回帧；看 COM21 上是否有 '[proto] bad frame' 打印")

        data = b"hello pico"
        got, ok = dev.echo(data)
        print("[4/6] ECHO  → %r  %s" % (got, "OK" if ok else "内容不一致"))
        if not ok:
            return fail("ECHO 内容不一致", "检查 CRC8 与帧长度是否两边一致（共用 hidproto.py）")

        try:
            before = dev.led_read()
            dev.led_write(not before)
            after = dev.led_read()
            dev.led_write(before)          # 还原
            led_ok = after != before
            print("[5/6] LED   → %d -> %d -> %d  %s" % (
                before, after, dev.led_read(), "OK" if led_ok else "无变化"))
        except Exception as e:
            return fail("LED 指令失败：%s" % e, "确认 main.py 主循环在跑")

        try:
            print("[6/6] TEMP  → %.2f °C    INFO → %s" % (dev.temp(), dev.info()))
        except Exception as e:
            return fail("TEMP/INFO 失败：%s" % e, "确认 main.py 主循环在跑")

    print(LINE)
    print(" 结论：自定义 HID 双向通讯 OK")
    print("   · 下行 PC→Pico：中断 OUT 64B 报告")
    print("   · 上行 Pico→PC：中断 IN  64B 响应")
    print("   · CDC 串口仅用于 REPL / 部署 / 激活，不参与数据通讯")
    print(LINE)
    print(" 下一步：python host/hid_demo.py stream 100 3   （主动上报）")
    print("         python host/hid_demo.py bench 200      （回环压测）")
    print("         python host/hid_gui.py                  （图形界面）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
