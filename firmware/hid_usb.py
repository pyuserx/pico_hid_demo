# hid_usb.py
# 在 MicroPython v1.28.0 (RP2040) 上用 machine.USBDevice 追加一个自定义 HID 接口。
#
# 背景：RP2040 官方固件没有 usb_hid 模块，只能走底层 USBDevice API。
# 做法：保留内置 CDC（COM 串口，方便调试），把自定义 HID 接口追加到内置
#       配置描述符后面：接口号 = itf_max(2)，端点号从 ep_max(3) 起。
#       64 字节中断 IN + 64 字节中断 OUT，Usage Page = 0xFF00（厂商自定义）。
#
# 重要：USB 回调运行在中断上下文，里面只能改标志/入队，
#       绝对不能 print()、也不能 submit_xfer()，否则 USB 会卡死。

import machine
import time

REPORT_LEN = 64
IN_EP = 0x83          # EP3 IN
OUT_EP = 0x03         # EP3 OUT

REPORT_DESC = bytes((
    0x06, 0x00, 0xFF,        # Usage Page (Vendor Defined 0xFF00)
    0x09, 0x01,              # Usage (0x01)
    0xA1, 0x01,              # Collection (Application)
    0x15, 0x00,              #   Logical Minimum (0)
    0x26, 0xFF, 0x00,        #   Logical Maximum (255)
    0x75, 0x08,              #   Report Size (8)
    0x95, 0x40,              #   Report Count (64)
    0x09, 0x02,              #   Usage (0x02)
    0x81, 0x02,              #   Input  (Data,Var,Abs) -> 64 字节 IN
    0x09, 0x03,              #   Usage (0x03)
    0x91, 0x02,              #   Output (Data,Var,Abs) -> 64 字节 OUT
    0xC0,                    # End Collection
))

_usb = None            # 必须惰性创建：导入本模块时若构造 USBDevice，
                       # 默认 builtin_driver 是 BUILTIN_NONE，会把内置 CDC 顶掉

_out_buf = bytearray(REPORT_LEN)
_tx_buf = bytearray(REPORT_LEN)
_tx_copy = bytearray(REPORT_LEN)      # send() 用的副本，避免和 IN 缓冲竞争
_ctrl_out = bytearray(REPORT_LEN)
_rx_queue = []
_tx_busy = False
_out_pending = False
_configured = False
_usb_on = False       # 只要 active(True) 成功就置 True，不依赖 open_itf_cb
_counts = {"rx": 0, "tx": 0, "err": 0}


def _hid_iface_desc(itf, ep_in, ep_out):
    return bytes((
        0x09, 0x04, itf, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,   # Interface (HID)
        0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22,             # HID descriptor
        len(REPORT_DESC), 0x00,
        0x07, 0x05, ep_in, 0x03, REPORT_LEN, 0x00, 0x01,      # EP IN  int 64B 1ms
        0x07, 0x05, ep_out, 0x03, REPORT_LEN, 0x00, 0x01,     # EP OUT int 64B 1ms
    ))


# ------------------------------------------------------------- USB 回调（中断）
def _control_xfer_cb(stage, xfer):
    bmRequestType = xfer[0]
    bRequest = xfer[1]
    wValue = xfer[2] | (xfer[3] << 8)
    wLength = xfer[6] | (xfer[7] << 8)
    reqtype = bmRequestType & 0x60

    if stage == 1:
        # GET_DESCRIPTOR: HID 报表描述符（wValue 高字节 = 0x22）
        if bRequest == 0x06 and (wValue >> 8) == 0x22:
            return REPORT_DESC[:wLength]
        if reqtype == 0x20:                     # 类请求
            if bRequest == 0x01:                # GET_REPORT
                return bytes(_tx_copy[:wLength])
            if bRequest == 0x09:                # SET_REPORT
                return _ctrl_out[:wLength]
            if bRequest in (0x02, 0x03):        # GET_IDLE / GET_PROTOCOL
                return b'\x00' * wLength
        return True

    if stage == 3:
        if reqtype == 0x20 and bRequest == 0x09:
            if len(_rx_queue) >= 16:
                del _rx_queue[0]
            _rx_queue.append(bytes(_ctrl_out[:wLength]))
            _counts["rx"] += 1
        return True

    return True


def _xfer_cb(ep, result, count):
    global _tx_busy, _out_pending, _configured

    if ep & 0x80:                                # IN 完成
        _tx_busy = False
        if result != 0:
            _counts["err"] += 1
        return

    _out_pending = False                         # OUT 完成
    if result == 0 and count:
        if len(_rx_queue) >= 16:
            del _rx_queue[0]
        _rx_queue.append(bytes(_out_buf[:count]))
        _counts["rx"] += 1
    else:
        _counts["err"] += 1


def _open_itf_cb(desc):
    global _configured
    _configured = True


def _reset_cb():
    global _configured, _tx_busy, _out_pending
    _configured = False
    _tx_busy = False
    _out_pending = False


# ----------------------------------------------------------------- 对外 API
def start():
    """在 boot.py 中调用：装配描述符并激活自定义 USB 设备。失败时回退到纯 CDC。"""
    global _configured, _usb, _usb_on
    bd = machine.USBDevice.BUILTIN_CDC
    if _usb is None:
        _usb = machine.USBDevice()

    cfg = bytearray(bd.desc_cfg)
    cfg[4] = bd.itf_max + 1                       # bNumInterfaces += 1
    iface = _hid_iface_desc(bd.itf_max, IN_EP, OUT_EP)
    total = len(cfg) + len(iface)
    cfg[2] = total & 0xFF                         # wTotalLength
    cfg[3] = (total >> 8) & 0xFF
    cfg.extend(iface)

    try:
        # 上电时 runtime 设备本来就是 inactive，这时千万别调 active(False)：
        # 实测它会把内置 CDC 一起摘掉，表现为设备整体掉出 USB 总线。
        # 只有在"运行期重复调用 start()"（已经激活过）时才需要先关闭。
        if _usb.active():
            _usb.active(False)
        _usb.builtin_driver = bd                  # 保留内置 CDC
        _usb.config(bd.desc_dev, bytes(cfg), None,
                    _open_itf_cb, _reset_cb, _control_xfer_cb, _xfer_cb)
        _usb.active(True)
        _configured = False
        _usb_on = True
        return True
    except Exception as e:
        # 回退：保持 CDC 可用，避免板子“消失”
        _usb_on = False
        try:
            _usb.active(True)
        except Exception:
            pass
        print("[hid] start failed:", e)
        return False


def is_configured():
    return _configured


def poll():
    """主循环里调用：保持一笔 OUT 传输常驻，等待主机下发。
    不依赖 open_itf_cb 是否被调用——host 设置配置后 submit_xfer 成功即进入正常态。"""
    global _out_pending, _configured
    if not _usb_on or _out_pending:
        return
    try:
        _out_pending = _usb.submit_xfer(OUT_EP, _out_buf)
        if _out_pending:
            _configured = True
    except OSError:
        _out_pending = False


def recv():
    if _rx_queue:
        return _rx_queue.pop(0)
    return None


def send(frame):
    """送一帧给主机（最多等上一笔 IN 完成 20ms）。"""
    global _tx_busy
    if not _usb_on:
        return False
    n = len(frame)
    if n > REPORT_LEN:
        n = REPORT_LEN
    for i in range(REPORT_LEN):
        _tx_copy[i] = frame[i] if i < n else 0

    t0 = time.ticks_ms()
    while _tx_busy:
        if time.ticks_diff(time.ticks_ms(), t0) > 20:
            return False
        time.sleep_ms(1)

    _tx_busy = True
    try:
        if not _usb.submit_xfer(IN_EP, _tx_copy):
            _tx_busy = False
            return False
    except OSError:
        _tx_busy = False
        return False
    _counts["tx"] += 1
    return True


def stats():
    return dict(_counts)
