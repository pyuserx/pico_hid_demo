"""sysmon.py — 把 PC 系统占用经自定义 HID 发到 Pico，驱动 I2C OLED12864 显示。

    python host/sysmon.py                       # 每 1 秒推送一次
    python host/sysmon.py --interval 0.5        # 改推送周期
    python host/sysmon.py --once                # 只推一帧
    python host/sysmon.py --host MYPC           # 覆盖屏上主机名
    python host/hid_demo.py sysmon --interval 1 # 等价入口

下位机需要 firmware/oled.py + 更新过的 main.py / hidproto.py；
OLED 没接或接线不对时固件仍会回 ACK，只是 ACK 首字节为 0。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysinfo  # noqa: E402 —— 零依赖采集，放在这里方便单独测试


def main(argv=None):
    ap = argparse.ArgumentParser(description="PC 系统占用 -> Pico OLED12864（自定义 HID）")
    ap.add_argument("--interval", type=float, default=1.0, help="推送周期，秒（默认 1.0）")
    ap.add_argument("--once", action="store_true", help="只推送一帧就退出")
    ap.add_argument("--host", default=None, help="覆盖屏上主机名（最多 16 个 ASCII 字符）")
    ap.add_argument("--mode", choices=["id0", "raw"], default=None,
                    help="跳过握手，强制指定写入格式")
    ap.add_argument("--no-auto", action="store_true", help="关闭自动激活 HID")
    a = ap.parse_args(argv)

    if a.no_auto:
        os.environ["PICO_HID_NO_AUTO_ACTIVATE"] = "1"

    # 延迟导入：--help 不需要 hidapi
    from pico_hid import PicoHID, PicoError

    try:
        dev = PicoHID(mode=a.mode)
    except (PicoError, OSError) as e:
        print("连接失败：", e)
        return 1

    interval = max(0.05, a.interval)
    print("采集方式 = %s（装了 psutil 就优先用它）" % sysinfo.SOURCE)
    with dev:
        try:
            print("已连接：", dev.ping())
        except Exception as e:
            print("PING 失败（继续推送）：", e)
        sysinfo.warmup()
        print("开始推送，Ctrl-C 退出；周期 %.2f s" % interval)
        try:
            while True:
                t0 = time.perf_counter()
                s = sysinfo.sample()
                host = a.host if a.host is not None else s["host"]
                try:
                    oled_ok = dev.sysinfo(s["cpu"], s["mem"], s["disk"],
                                          gpu=s["gpu"], host=host)
                    rtt = (time.perf_counter() - t0) * 1000
                    print("CPU %3d%%  MEM %3d%%  DISK %3d%%  OLED=%s  RTT=%.1f ms" % (
                        s["cpu"], s["mem"], s["disk"],
                        "OK" if oled_ok else "无屏/失败", rtt))
                    if not oled_ok:
                        print("  提示：检查 firmware/oled.py 的 SDA/SCL/ADDR 与接线；"
                              "固件仍回 ACK，HID 链路不受影响")
                except (TimeoutError, PicoError, OSError) as e:
                    print("发送失败：", e)
                if a.once:
                    break
                left = interval - (time.perf_counter() - t0)
                time.sleep(left if left > 0 else 0.01)
        except KeyboardInterrupt:
            print("\n已停止（OLED 保留最后一帧，5 秒后固件显示 NO SIGNAL）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
