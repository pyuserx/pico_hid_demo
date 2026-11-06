"""把 tools/activate.py 的激活能力内嵌到上位机连接流程：开上位机即自动连。

只在「找不到 vendor HID」时触发；HID 已在线则完全不碰串口。
配套保护：
  - 进程内非阻塞锁：网页版多线程 / 并发命令时只激活一次；
  - 失败冷却 15 秒：避免状态栏轮询反复打串口、反复 Ctrl-C 下位机；
  - 环境变量 PICO_HID_NO_AUTO_ACTIVATE=1 可彻底关闭（只想看设备列表时用）。

激活逻辑只有一份，就是 tools/activate.py 的 ensure_ready()。
"""
from __future__ import annotations

import os
import sys
import time
import threading

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

DISABLE_ENV = "PICO_HID_NO_AUTO_ACTIVATE"
COOLDOWN = 15.0

_lock = threading.Lock()
_last_fail = 0.0
_last_msg = ""


def is_enabled():
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in ("1", "true", "yes", "on")


def last_message():
    """最近一次自动激活失败的原因，供错误信息拼接。"""
    return _last_msg


def ensure_hid(wait=15, log=print):
    """找不到 HID 时调用。返回 (ok, msg)；失败原因同时存进 last_message()。"""
    global _last_fail, _last_msg
    import activate  # tools/activate.py

    if not _lock.acquire(blocking=False):
        return False, "另一个线程正在自动激活 HID，请稍候重试"
    try:
        now = time.monotonic()
        if _last_fail and now - _last_fail < COOLDOWN:
            return False, "自动激活刚失败（%d 秒内不重试）：%s" % (
                int(COOLDOWN - (now - _last_fail)), _last_msg)
        log("[auto] 未找到自定义 HID，尝试通过 CDC 串口自动激活 ...")
        ok, msg = activate.ensure_ready(wait=wait, log=log)
        if ok:
            _last_fail = 0.0
            log("[auto] " + msg)
        else:
            _last_fail = time.monotonic()
            _last_msg = msg
            log("[auto] 自动激活失败：" + msg)
        return ok, msg
    finally:
        _lock.release()
