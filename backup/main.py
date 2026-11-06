# main.py — 下位机主循环：处理自定义 HID 协议命令
#
# 依赖 boot.py 里已经调用的 hid_usb.start()：USB 回调只置标志，
# OUT 端点由主循环 poll() 续挂，IN 响应由 send() 发出。

import time
import machine
import hid_usb
import hidproto as P
import oled

LED_PIN = 25
SYS_TIMEOUT_MS = 5000   # 超过这个时间没收到 PC 数据就显示 NO SIGNAL

_led = machine.Pin(LED_PIN, machine.Pin.OUT)
_adc = [machine.ADC(i) for i in range(4)]
_adc_temp = machine.ADC(4)

_stream_ms = 0          # 0 = 不流式上报
_next_stream = 0
_stream_counter = 0

_oled_dev = None        # init() 失败时为 None，SYS_INFO 仍正常回 ACK
_last_sys_ms = 0        # 最近一次收到 PC 系统信息
_sys_lost = False       # 已显示过 NO SIGNAL，避免重复刷屏

_T0 = time.ticks_ms()

INFO = b"Pico RP2040 custom HID v1.0"


def _uptime_ms():
    return time.ticks_ms() & 0xFFFFFFFF


def _adc_mv(ch):
    raw = _adc[ch].read_u16()
    # RP2040 ADC: 12 bit, 3.3V 参考，read_u16() 返回 16 位（低 4 位为 0）
    return (raw * 3300) // 65535


def _temp_c100():
    raw = _adc_temp.read_u16()
    volt = raw * 3.3 / 65535.0
    return int((27 - (volt - 0.706) / 0.001721) * 100)


def _reply(cmd, seq, payload=b""):
    hid_usb.send(P.pack((cmd | 0x80) & 0xFF, seq, payload))


def handle(frame):
    global _stream_ms, _next_stream, _last_sys_ms, _sys_lost
    try:
        cmd, seq, payload = P.unpack(frame)
    except ValueError as e:
        print("[proto] bad frame:", e)
        return

    if cmd == P.CMD_PING:
        _reply(P.CMD_PING, seq, INFO)

    elif cmd == P.CMD_ECHO:
        _reply(P.CMD_ECHO, seq, payload)

    elif cmd == P.CMD_GET_INFO:
        info = "Pico HID | uptime=%d ms | fw=1.0" % _uptime_ms()
        _reply(P.CMD_GET_INFO, seq, info.encode())

    elif cmd == P.CMD_LED_WRITE:
        val = payload[0] if payload else 0
        _led.value(1 if val else 0)
        _reply(P.CMD_LED_WRITE, seq, bytes((_led.value(),)))

    elif cmd == P.CMD_LED_READ:
        _reply(P.CMD_LED_READ, seq, bytes((_led.value(),)))

    elif cmd == P.CMD_ADC_READ:
        ch = payload[0] if payload else 0
        if ch > 3:
            _reply(P.ERR_BAD_FRAME, seq, b"bad channel")
        else:
            mv = _adc_mv(ch)
            _reply(P.CMD_ADC_READ, seq, bytes((mv & 0xFF, (mv >> 8) & 0xFF)))

    elif cmd == P.CMD_TEMP_READ:
        t = _temp_c100()
        _reply(P.CMD_TEMP_READ, seq, bytes((t & 0xFF, (t >> 8) & 0xFF)))

    elif cmd == P.CMD_UPTIME:
        up = _uptime_ms()
        _reply(P.CMD_UPTIME, seq, up.to_bytes(4, "little"))

    elif cmd == P.CMD_STREAM_START:
        ms = payload[0] | (payload[1] << 8) if len(payload) >= 2 else 100
        if ms < 10:
            ms = 10
        _stream_ms = ms
        _next_stream = time.ticks_ms()
        _reply(P.CMD_STREAM_START, seq, bytes((ms & 0xFF, (ms >> 8) & 0xFF)))

    elif cmd == P.CMD_STREAM_STOP:
        _stream_ms = 0
        _reply(P.CMD_STREAM_STOP, seq, b"stopped")

    elif cmd == P.CMD_GET_STATS:
        s = hid_usb.stats()
        _reply(P.CMD_GET_STATS, seq,
               bytes((s["rx"] & 0xFF, s["tx"] & 0xFF, s["err"] & 0xFF)))

    elif cmd == P.CMD_SYS_INFO:
        info = P.unpack_sysinfo(payload)
        if info is None:
            _reply(P.ERR_BAD_FRAME, seq, b"bad sysinfo")
        else:
            ok = False
            try:
                if _oled_dev is not None:
                    ok = oled.show_system(_oled_dev, info)
            except Exception as e:
                print("[oled] show failed:", e)
            _last_sys_ms = time.ticks_ms()
            _sys_lost = False
            # ACK 第 0 字节：1 = OLED 已刷新，0 = 屏不可用
            _reply(P.CMD_SYS_INFO, seq, bytes((1 if ok else 0,)))

    else:
        _reply(P.ERR_BAD_CMD, seq, bytes((cmd,)))


def stream_tick():
    global _next_stream, _stream_counter
    if not _stream_ms:
        return
    if time.ticks_diff(time.ticks_ms(), _next_stream) < 0:
        return
    _next_stream = time.ticks_add(_next_stream, _stream_ms)
    _stream_counter += 1
    up = _uptime_ms()
    mv = _adc_mv(0)
    payload = bytes((
        up & 0xFF, (up >> 8) & 0xFF, (up >> 16) & 0xFF, (up >> 24) & 0xFF,
        _stream_counter & 0xFF, (_stream_counter >> 8) & 0xFF,
        mv & 0xFF, (mv >> 8) & 0xFF,
        _led.value(),
    ))
    if not hid_usb.send(P.pack(P.EVT_STATUS, 0, payload)):
        # IN 端点忙，下一轮再发
        _next_stream = time.ticks_ms()


def sys_tick():
    """上位机停止推送时提示一次 NO SIGNAL，不反复刷屏。"""
    global _sys_lost
    if _oled_dev is None or _last_sys_ms == 0 or _sys_lost:
        return
    if time.ticks_diff(time.ticks_ms(), _last_sys_ms) < SYS_TIMEOUT_MS:
        return
    _sys_lost = True
    try:
        oled.show_banner(_oled_dev, "NO PC SIGNAL", "WAITING USB...")
    except Exception:
        pass


def main():
    global _oled_dev
    print("[main] custom HID demo started")
    _oled_dev = oled.init()
    if _oled_dev is None:
        print("[main] OLED unavailable, SYS_INFO still acked")
    while True:
        hid_usb.poll()
        frame = hid_usb.recv()
        if frame is not None:
            handle(frame)
        stream_tick()
        sys_tick()
        time.sleep_ms(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[main] stopped (Ctrl-C)")
