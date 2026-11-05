# main.py — 下位机主循环：装配自定义 HID + 处理协议命令
#
# boot.py 阶段不碰 USB（会打断主机枚举导致整板 USB 挂死），
# HID 在这里、系统就绪后激活。USB 回调只置标志，
# OUT 端点由主循环 poll() 续挂，IN 响应由 send() 发出。

import time
import machine
import hid_usb
import hidproto as P

LED_PIN = 25
SAFE_PIN = 14            # 短接 GND 上电 = 安全模式，跳过 HID 激活

_led = machine.Pin(LED_PIN, machine.Pin.OUT)
_adc = [machine.ADC(i) for i in range(4)]
_adc_temp = machine.ADC(4)

_stream_ms = 0          # 0 = 不流式上报
_next_stream = 0
_stream_counter = 0

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
    global _stream_ms, _next_stream
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


def _safe_mode():
    try:
        return machine.Pin(SAFE_PIN, machine.Pin.IN, machine.Pin.PULL_UP).value() == 0
    except Exception:
        return False


def _start_usb():
    """系统就绪后再重新配置 USBDevice 追加 HID 接口。

    主机枚举内置 CDC 需要一点时间；立刻重配 USB 会打断枚举，
    实测会让整块板掉出 USB 总线。这里留出 500ms 余量。
    """
    if _safe_mode():
        print("[main] SAFE mode (GP14=GND): HID disabled, CDC only")
        return False
    time.sleep_ms(500)
    ok = hid_usb.start()
    print("[main] hid_usb.start():", ok)
    return ok


def main():
    print("[main] custom HID demo started")
    _start_usb()
    while True:
        hid_usb.poll()
        frame = hid_usb.recv()
        if frame is not None:
            handle(frame)
        stream_tick()
        time.sleep_ms(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[main] stopped (Ctrl-C)")
