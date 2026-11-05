# hid_vendor.py -- Pico 侧：自定义 vendor-defined HID 接口
#
# 在 boot.py 里 import 本模块即完成 USB 重配置（CDC + 自定义 HID 复合设备）。
# main.py 再从这里取 IFACE 单例收发数据。
#
# 依赖：micropython-lib 的 usb-device / usb-device-hid（见 ../lib/usb/device/）
# 固件要求：MicroPython >= 1.24（需要 machine.USBDevice），实测 v1.28.0 / RP2040。
#
# 关键安全点：builtin_driver=True 必须保留，否则内置 CDC(COM21) 会消失，
#             没有串口 REPL 就很难改回来了。
from micropython import const
import usb.device
from usb.device.hid import HIDInterface

# ---- USB / 报告层 ----------------------------------------------------------
REPORT_ID = const(0x01)
WIRE_LEN = const(64)                 # Report ID(1) + payload(63)，正好一个中断包
PAYLOAD_LEN = const(WIRE_LEN - 1)    # 63
MAX_DATA = const(PAYLOAD_LEN - 5)    # 58：SOF+CMD+SEQ+LEN+CHK 共 5 字节
SOF = const(0x55)
EP_MPS = const(64)
RX_QUEUE_MAX = const(16)

# 命令码 —— 必须与 host/pico_hid.py 里的定义保持一致
CMD_PING = const(0x01)
CMD_ECHO = const(0x02)
CMD_LED = const(0x03)
CMD_ADC = const(0x04)
CMD_TEMP = const(0x05)
CMD_GPIO = const(0x06)
CMD_INFO = const(0x07)
CMD_STREAM = const(0x08)
CMD_BENCH = const(0x09)
CMD_ERR = const(0x7F)                # 响应时 |0x80 -> 0xFF

# Vendor Defined Usage Page 0xFF00 / Usage 0x01
# Report ID 1：63 字节 Input + 63 字节 Output
# fmt: off
_REPORT_DESC = bytes((
    0x06, 0x00, 0xFF,        # Usage Page (Vendor Defined 0xFF00)
    0x09, 0x01,              # Usage (Vendor 1)
    0xA1, 0x01,              # Collection (Application)
        0x85, REPORT_ID,     #   Report ID (1)
        0x09, 0x02,          #   Usage (Vendor 2 = IN)
        0x15, 0x00,          #   Logical Minimum (0)
        0x26, 0xFF, 0x00,    #   Logical Maximum (255)
        0x75, 0x08,          #   Report Size (8)
        0x95, PAYLOAD_LEN,   #   Report Count (63)
        0x81, 0x02,          #   Input (Data, Var, Abs)
        0x09, 0x03,          #   Usage (Vendor 3 = OUT)
        0x95, PAYLOAD_LEN,   #   Report Count (63)
        0x91, 0x02,          #   Output (Data, Var, Abs)
    0xC0,                    # End Collection
))
# fmt: on


class VendorHIDInterface(HIDInterface):
    """官方 HIDInterface 只建 1 个 IN 端点、靠控制传输 SET_REPORT 收数据。

    这里改成 IN + OUT 双中断端点、wMaxPacketSize=64，双向都是标准 HID
    report，吞吐和兼容性都更好；同时保留 SET_REPORT 兜底路径。
    """

    def __init__(self):
        super().__init__(
            _REPORT_DESC,
            set_report_buf=bytearray(WIRE_LEN),   # 兼容走控制传输的主机
            interface_str="Pico Vendor HID",
        )
        self._out_ep = None
        self._rx_buf = bytearray(WIRE_LEN)
        self.rx_queue = []
        self.rx_count = 0
        self.rx_dropped = 0
        self.tx_count = 0

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # 与父类同构，但 bNumEndpoints=2、wMaxPacketSize=64，并追加 OUT 端点。
        # 注意：core.py 会用 dummy 参数先调用一次来算长度，本函数必须幂等。
        desc.interface(
            itf_num, 2,
            0x03,        # bInterfaceClass = HID
            0x00,        # bInterfaceSubClass = None (非 boot)
            0x00,        # bInterfaceProtocol = None
            len(strs) if self.interface_str else 0,
        )
        if self.interface_str:
            strs.append(self.interface_str)
        self.get_hid_descriptor(desc)

        self._int_ep = ep_num | 0x80
        self._out_ep = ep_num
        interval = getattr(self, "interval_ms", 8)
        desc.endpoint(self._int_ep, "interrupt", EP_MPS, interval)
        desc.endpoint(self._out_ep, "interrupt", EP_MPS, interval)

        self.idle_rate = 0
        self.protocol = 1           # report protocol

    def num_eps(self):
        return 1                    # IN/OUT 共用一个端点号

    # ---- 接收 --------------------------------------------------------------
    def on_open(self):
        super().on_open()
        self._arm_rx()

    def _arm_rx(self):
        try:
            self.submit_xfer(self._out_ep, self._rx_buf, self._rx_done)
        except (RuntimeError, ValueError, KeyError):
            pass                    # 未枚举 / 复位瞬间，等下次 on_open

    def _rx_done(self, ep, result, nbytes):
        if result == 0 and nbytes:
            payload = self._normalize(memoryview(self._rx_buf)[:nbytes])
            if payload is not None:
                self._push(payload)
        self._arm_rx()              # 立刻重新挂上 OUT 传输，不丢后续包

    def on_set_report(self, report_data, report_id, report_type):
        # 没有 OUT 中断端点的主机/驱动会走 HID 类控制请求 SET_REPORT，
        # 此时数据里通常不含 Report ID（ID 在 wValue 低字节）。
        payload = self._normalize(report_data)
        if payload is not None:
            self._push(payload)
        return True

    def _normalize(self, raw):
        """把「含 ID 的 64B 中断报告」或「不含 ID 的控制报告」统一成 63B payload。"""
        if not raw:
            return None
        body = raw[1:] if raw[0] == REPORT_ID else raw
        p = bytearray(PAYLOAD_LEN)
        take = min(len(body), PAYLOAD_LEN)
        p[:take] = bytes(body[:take])
        if p[0] != SOF:
            return None
        return p

    def _push(self, payload):
        if len(self.rx_queue) < RX_QUEUE_MAX:
            self.rx_queue.append(payload)
            self.rx_count += 1
        else:
            self.rx_dropped += 1

    # ---- 发送 --------------------------------------------------------------
    def send_frame(self, payload):
        p = bytearray(PAYLOAD_LEN)
        take = min(len(payload), PAYLOAD_LEN)
        p[:take] = bytes(payload[:take])
        ok = self.send_report(bytes((REPORT_ID,)) + bytes(p))
        if ok:
            self.tx_count += 1
        return ok


# 模块导入即生效：CDC(REPL) + HID 复合设备。
# 不覆盖 VID/PID/Serial，保持 Windows 上的 COM21 不变；上位机靠 usage_page 找设备。
IFACE = VendorHIDInterface()
usb.device.get().init(
    IFACE,
    builtin_driver=True,            # <<< 保住 COM21，千万别删
    manufacturer_str="WorkBuddy",
    product_str="Pico Custom HID Demo",
)
