# main.py -- 自定义 HID 通讯 Demo 的协议处理循环（RP2040 / MicroPython v1.28）
#
# 帧格式（63 字节定长 payload，不足补 0）：
#   [0]    0x55        SOF
#   [1]    CMD         命令；响应为 CMD|0x80，0xFF 表示错误
#   [2]    SEQ         序列号，请求原样回显
#   [3]    LEN         数据长度 0..58
#   [4..]  DATA        LEN 字节
#   [4+LEN] CHECKSUM   sum(payload[0 .. 4+LEN-1]) & 0xFF
# 其余字节填 0。USB 层再加 1 字节 Report ID=0x01 组成 64 字节报告。
import time
import machine
import struct
import sys
import gc

try:
    import hid_vendor
    from hid_vendor import (
        IFACE, SOF, PAYLOAD_LEN, MAX_DATA,
        CMD_PING, CMD_ECHO, CMD_LED, CMD_ADC, CMD_TEMP,
        CMD_GPIO, CMD_INFO, CMD_STREAM, CMD_BENCH, CMD_ERR,
    )
except Exception as _e:
    print("hid_vendor unavailable, HID demo not started:", _e)
    raise

try:
    LED = machine.Pin("LED", machine.Pin.OUT)
except Exception:
    LED = machine.Pin(25, machine.Pin.OUT)

_ADC_TEMP = machine.ADC(4)
_VREF = 3.3 / 65535.0
_START = time.ticks_ms()
_stream = {"left": 0, "idx": 0, "period": 50, "next": 0, "seq": 0}


def uptime_ms():
    return time.ticks_diff(time.ticks_ms(), _START) & 0xFFFFFFFF


def read_temp_x100():
    # RP2040 内部温度传感器：V = 0.706 - 0.001721 * (T - 27)
    v = _ADC_TEMP.read_u16() * _VREF
    return int((27.0 - (v - 0.706) / 0.001721) * 100)


def build_reply(cmd, seq, data=b""):
    p = bytearray(PAYLOAD_LEN)
    data = bytes(data)[:MAX_DATA]
    p[0] = SOF
    p[1] = (cmd | 0x80) & 0xFF
    p[2] = seq & 0xFF
    p[3] = len(data)
    p[4:4 + len(data)] = data
    p[4 + len(data)] = sum(p[:4 + len(data)]) & 0xFF
    return p


def parse_frame(p):
    if len(p) < 5 or p[0] != SOF:
        return None
    n = p[3]
    if n > MAX_DATA or 4 + n >= len(p):
        return None
    if p[4 + n] != (sum(p[:4 + n]) & 0xFF):
        return None
    return p[1], p[2], bytes(p[4:4 + n])


def send(cmd, seq, data=b""):
    return IFACE.send_frame(build_reply(cmd, seq, data))


def send_error(seq, code):
    send(CMD_ERR, seq, b"E%02d" % code)


def handle(cmd, seq, d):
    global _stream

    if cmd == CMD_PING:
        LED.toggle()
        payload = b"PONG" + struct.pack("<II", uptime_ms(), gc.mem_free())
        send(cmd, seq, payload)

    elif cmd == CMD_ECHO:
        send(cmd, seq, d)

    elif cmd == CMD_LED:
        # d[0]: 0=off 1=on 2=toggle 0xFF=read
        op = d[0] if d else 0xFF
        if op == 0:
            LED.off()
        elif op == 1:
            LED.on()
        elif op == 2:
            LED.toggle()
        elif op != 0xFF:
            send_error(seq, 2)      # 非法参数
            return
        send(cmd, seq, bytes((LED.value(),)))

    elif cmd == CMD_ADC:
        ch = d[0] if d else 4
        if ch > 4 or ch < 0:
            send_error(seq, 3)
            return
        try:
            raw = _ADC_TEMP.read_u16() if ch == 4 else machine.ADC(ch).read_u16()
            mv = int(raw * _VREF * 1000)
            send(cmd, seq, struct.pack("<BHH", ch, raw, mv))
        except Exception:
            send_error(seq, 4)

    elif cmd == CMD_TEMP:
        send(cmd, seq, struct.pack("<h", read_temp_x100()))

    elif cmd == CMD_GPIO:
        # d = [pin, value]；value=0xFF 表示读取（带上拉）
        if len(d) < 2:
            send_error(seq, 2)
            return
        pin_no, value = d[0], d[1]
        if pin_no > 28:
            send_error(seq, 3)
            return
        try:
            if value == 0xFF:
                pin = machine.Pin(pin_no, machine.Pin.IN, machine.Pin.PULL_UP)
                value = pin.value()
            else:
                pin = machine.Pin(pin_no, machine.Pin.OUT)
                pin.value(1 if value else 0)
                value = 1 if value else 0
            send(cmd, seq, bytes((pin_no, value)))
        except Exception:
            send_error(seq, 4)

    elif cmd == CMD_INFO:
        ver = ".".join(str(x) for x in sys.implementation.version[:3])
        txt = "rp2 MP%s %dMHz free=%dB rx=%d tx=%d drop=%d" % (
            ver, machine.freq() // 1000000, gc.mem_free(),
            IFACE.rx_count, IFACE.tx_count, IFACE.rx_dropped,
        )
        send(cmd, seq, txt.encode())

    elif cmd == CMD_STREAM:
        # d = [count_hi, count_lo, period_ms]
        if len(d) < 3:
            send_error(seq, 2)
            return
        count = (d[0] << 8) | d[1]
        period = d[2] if d[2] >= 5 else 5
        if count > 5000:
            count = 5000
        _stream = {"left": count, "idx": 0, "period": period,
                   "next": 0, "seq": seq}
        send(cmd, seq, struct.pack("<HB", count, period))

    elif cmd == CMD_BENCH:
        send(cmd, seq, d[:4] + struct.pack("<II", IFACE.rx_count, IFACE.tx_count))

    else:
        send_error(seq, 1)          # 未知命令


def service_stream():
    """非阻塞地按节拍主动上报，每轮最多发一帧，避免饿死接收。"""
    if _stream["left"] <= 0:
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, _stream["next"]) < 0:
        return
    idx = _stream["idx"]
    payload = struct.pack("<HII", idx, uptime_ms(), _ADC_TEMP.read_u16())
    if send(CMD_STREAM, _stream["seq"], payload):
        _stream["idx"] = idx + 1
        _stream["left"] -= 1
        _stream["next"] = time.ticks_add(now, _stream["period"])
    else:
        _stream["next"] = time.ticks_add(now, 5)   # IN 端点忙，稍后重试


print("Pico HID demo ready, waiting for host...")
while True:
    q = IFACE.rx_queue
    if q:
        frame = parse_frame(q.pop(0))
        if frame is not None:
            try:
                handle(frame[0], frame[1], frame[2])
            except Exception as e:
                send_error(frame[1], 9)
                print("handler error:", e)
    else:
        service_stream()
        time.sleep_ms(1)
