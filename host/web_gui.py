"""Pico 自定义 HID 通讯 Demo —— 网页版上位机（零第三方前端依赖）。

    python host/web_gui.py                # 启动后自动打开 http://127.0.0.1:8766
    python host/web_gui.py --port 9000 --no-browser

为什么有这个：本机 Python 是精简版，没有 tkinter，hid_gui.py 无法启动。
这里用 Python 标准库 http.server 起一个本地服务，hidapi 仍在后端，
浏览器只负责界面，不需要 WebHID / 不需要联网 / 不需要装任何东西。

命令实现直接复用 hid_demo.run()，与命令行上位机行为完全一致。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "firmware"))

import hid_demo  # noqa: E402  复用命令行上位机的命令实现
import hidproto as P  # noqa: E402
from pico_hid import PicoHID, PicoError, find_device  # noqa: E402

_lock = threading.Lock()          # hidapi 句柄不是线程安全的，所有访问串行化
_dev: PicoHID | None = None
_dev_label = "未连接"


def _ensure():
    global _dev, _dev_label
    if _dev is None:
        info = find_device()
        if info is None:
            raise PicoError(
                "未找到 Pico 自定义 HID（已自动尝试激活，原因见服务端 [auto] 日志）。"
                "常见原因：Thonny 占用 COM 口；也可手动跑 tools/activate.py 或双击 activate.bat")
        _dev = PicoHID(info=info)
        _dev_label = "%04X:%04X  %s  [%s]" % (
            info["vendor_id"], info["product_id"],
            info.get("product_string") or "HID", _dev.mode)
    return _dev


def _reset():
    global _dev, _dev_label
    if _dev is not None:
        try:
            _dev.close()
        except Exception:
            pass
    _dev = None
    _dev_label = "未连接"


def _status():
    with _lock:
        connected = _dev is not None
        label = _dev_label
    return {"connected": connected, "device": label,
            "protocol": "64B 报告 · 0xA5 · CRC8 · 无 Report ID"}


def _live():
    """状态栏实时数据：温度 / uptime / 计数。不写通讯日志。

    未连接时直接返回 ok=False，不主动抢占设备、也不抛异常打断 HTTP 线程。
    """
    with _lock:
        if _dev is None:
            return {"ok": False, "error": "尚未连接，点「连接」后再看实时数据"}
        try:
            return {"ok": True,
                    "temp": _dev.temp(),
                    "uptime": _dev.uptime(),
                    "stats": _dev.stats()}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _command(argv):
    """执行一条上位机命令，返回 (ok, text)。"""
    if not argv:
        return False, "空命令"
    t0 = time.perf_counter()
    with _lock:
        try:
            if argv[0].lower() == "connect":
                _reset()
                d = _ensure()
                return True, "已连接：%s" % _dev_label
            if argv[0].lower() == "disconnect":
                _reset()
                return True, "已断开"
            d = _ensure()
            text = hid_demo.run(d, argv)
            ms = (time.perf_counter() - t0) * 1000
            return True, "%s   · %.1f ms" % (text, ms)
        except (PicoError, OSError, TimeoutError, ValueError, IndexError) as e:
            return False, "%s：%s" % (type(e).__name__, e)
        except Exception:
            return False, traceback.format_exc(limit=3)


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pico 自定义 HID · 上位机</title>
<style>
  :root{
    --bg:#f4f6f8; --card:#ffffff; --line:#dfe4ea; --fg:#1f2328;
    --dim:#6a737d; --blue:#2b6cb0; --green:#1e7e34; --red:#c0392b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{background:var(--card);border-bottom:1px solid var(--line);
         padding:12px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.3px}
  .badge{font:12px Consolas,monospace;padding:4px 10px;border-radius:20px;
         background:#eef2f7;color:var(--dim);border:1px solid var(--line)}
  .badge.on{background:#e8f5ec;color:var(--green);border-color:#bfe3c9}
  .badge.off{background:#fdecea;color:var(--red);border-color:#f3c6c0}
  .spacer{flex:1}
  button{font:13px inherit;cursor:pointer;border:1px solid var(--line);
         background:var(--card);color:var(--fg);border-radius:6px;
         padding:7px 12px;transition:.12s}
  button:hover{border-color:var(--blue);color:var(--blue)}
  button:active{transform:translateY(1px)}
  button.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
  button.primary:hover{background:#245a94;color:#fff}
  main{display:flex;gap:14px;padding:14px 18px;align-items:stretch;height:calc(100vh - 108px)}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
  .left{width:330px;flex:none;display:flex;flex-direction:column;gap:10px;overflow:auto}
  .right{flex:1;display:flex;flex-direction:column;min-width:0}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);
     margin:0 0 8px;font-weight:600}
  .params{display:grid;grid-template-columns:78px 1fr;gap:6px 8px;align-items:center}
  .params label{text-align:right;color:var(--dim);font-size:12px}
  input{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:5px;
        font:13px Consolas,monospace;background:#fbfcfd;color:var(--fg)}
  input:focus{outline:none;border-color:var(--blue)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .grid button{text-align:left;padding:7px 10px;font-size:12.5px}
  .grid button.wide{grid-column:1 / -1;text-align:center}
  .loghead{display:flex;align-items:center;gap:8px;margin-bottom:8px}
  .loghead h2{margin:0;flex:1}
  #log{flex:1;overflow:auto;background:#fbfcfd;border:1px solid var(--line);
       border-radius:6px;padding:10px;font:12.5px/1.7 Consolas,"Courier New",monospace;
       white-space:pre-wrap;word-break:break-all}
  .tx{color:var(--dim)} .ok{color:var(--green)} .err{color:var(--red)} .info{color:var(--blue)}
  footer{background:var(--card);border-top:1px solid var(--line);padding:8px 18px;
         display:flex;gap:22px;align-items:center;font:12px Consolas,monospace;color:var(--dim);
         flex-wrap:wrap}
  footer b{color:var(--fg);font-weight:600}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;margin-right:6px}
</style>
</head>
<body>
<header>
  <h1>Pico 自定义 HID · 上位机</h1>
  <span id="badge" class="badge">连接中…</span>
  <span class="spacer"></span>
  <button onclick="send(['connect'])">连接</button>
  <button onclick="send(['disconnect'])">断开</button>
  <button onclick="clearLog()">清空日志</button>
</header>

<main>
  <section class="panel left">
    <div>
      <h2>参数</h2>
      <div class="params">
        <label>文本 / 次数</label><input id="a1" value="hello pico">
        <label>通道</label><input id="ch" value="0">
        <label>周期 ms</label><input id="ms" value="100">
        <label>采样 秒</label><input id="sec" value="2">
      </div>
    </div>
    <div>
      <h2>基础命令</h2>
      <div class="grid">
        <button onclick="send(['ping'])">PING 握手</button>
        <button onclick="send(['info'])">INFO 固件信息</button>
        <button onclick="send(['echo',a1()])">ECHO 回显</button>
        <button onclick="send(['uptime'])">UPTIME 运行时间</button>
        <button onclick="send(['temp'])">TEMP 芯片温度</button>
        <button onclick="send(['stats'])">STATS 计数</button>
      </div>
    </div>
    <div>
      <h2>IO 与采样</h2>
      <div class="grid">
        <button onclick="send(['led','on'])">LED 点亮</button>
        <button onclick="send(['led','off'])">LED 熄灭</button>
        <button onclick="send(['led','read'])">LED 读取</button>
        <button onclick="send(['adc',ch()])">ADC 采样</button>
      </div>
    </div>
    <div>
      <h2>压力 / 上报</h2>
      <div class="grid">
        <button onclick="send(['stream',ms(),sec()])">STREAM 采样</button>
        <button onclick="send(['bench',n()])">BENCH 压测</button>
        <button class="wide" onclick="quick()">一键全测（PING/ECHO/LED/TEMP/INFO）</button>
      </div>
    </div>
  </section>

  <section class="panel right">
    <div class="loghead">
      <h2>通讯日志</h2>
      <span id="proto" style="font:11px Consolas,monospace;color:var(--dim)"></span>
    </div>
    <div id="log"></div>
  </section>
</main>

<footer>
  <span><span class="dot"></span><span id="dev">—</span></span>
  <span>温度 <b id="temp">—</b></span>
  <span>Uptime <b id="uptime">—</b></span>
  <span>下位机 RX <b id="rx">—</b> / TX <b id="tx">—</b> / ERR <b id="err">—</b></span>
  <span class="spacer"></span>
  <label style="display:flex;align-items:center;gap:5px;cursor:pointer">
    <input id="auto" type="checkbox" checked style="width:auto">自动刷新
  </label>
</footer>

<script>
const $ = id => document.getElementById(id);
const a1 = () => $('a1').value || 'hello pico';
const ch = () => $('ch').value || '0';
const ms = () => $('ms').value || '100';
const sec = () => $('sec').value || '2';
const n = () => (/^\d+$/.test($('a1').value) ? $('a1').value : '100');

function line(text, cls){
  const box = $('log');
  const t = new Date().toTimeString().slice(0,8);
  const span = document.createElement('span');
  span.className = cls || '';
  span.textContent = '[' + t + '] ' + text + '\n';
  box.appendChild(span);
  box.scrollTop = box.scrollHeight;
}
function clearLog(){ $('log').textContent = ''; }

let busy = false;
async function send(argv){
  if (busy) { line('上一条命令还在执行，请稍候…', 'info'); return; }
  busy = true;
  line('→ ' + argv.join(' '), 'tx');
  try{
    const r = await fetch('/api/cmd', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({argv})});
    const j = await r.json();
    line(j.ok ? '← ' + j.text : '✗ ' + j.text, j.ok ? 'ok' : 'err');
  }catch(e){ line('请求失败：' + e, 'err'); }
  finally{ busy = false; refresh(); }
}
async function quick(){
  for (const c of [['ping'], ['echo', a1()], ['led','on'], ['led','read'],
                   ['led','off'], ['temp'], ['info']]){
    await send(c);
  }
}
async function refresh(){
  try{
    const r = await fetch('/api/status'); const j = await r.json();
    const b = $('badge');
    b.textContent = j.connected ? '● 已连接' : '○ 未连接';
    b.className = 'badge ' + (j.connected ? 'on' : 'off');
    $('dev').textContent = j.device;
    $('proto').textContent = j.protocol;
  }catch(e){ $('badge').textContent = '服务未响应'; }
}
async function live(){
  if (!$('auto').checked) return;
  try{
    const r = await fetch('/api/live'); const j = await r.json();
    if (!j.ok) return;
    $('temp').textContent = j.temp == null ? '—' : j.temp.toFixed(2) + ' °C';
    $('uptime').textContent = (j.uptime/1000).toFixed(1) + ' s';
    $('rx').textContent = j.stats.rx ?? '—';
    $('tx').textContent = j.stats.tx ?? '—';
    $('err').textContent = j.stats.err ?? '—';
  }catch(e){}
}
line('网页版上位机已启动 · 后端 hidapi，浏览器只负责界面', 'info');
line('协议：64B HID 报告 · 0xA5 帧头 · CRC8 · 无 Report ID', 'info');
send(['connect']);
refresh();
setInterval(refresh, 5000);
setInterval(live, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PicoHID/1.0"

    def log_message(self, fmt, *args):
        pass                      # 静默，避免每次轮询刷屏

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, HTML, "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(_status())
        elif path == "/api/live":
            self._json(_live())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/cmd":
            self._json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            argv = [str(x) for x in body.get("argv", [])]
            ok, text = _command(argv)
            self._json({"ok": ok, "text": text})
        except Exception as e:
            self._json({"ok": False, "text": "%s: %s" % (type(e).__name__, e)}, 400)


def main():
    ap = argparse.ArgumentParser(description="Pico 自定义 HID 网页版上位机")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    # Windows 上端口可能被别的进程占用或落在保留区间，自动往后找一个能绑的
    srv = None
    for port in range(a.port, a.port + 10):
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            a.port = port
            break
        except OSError as e:
            print("端口 %d 不可用（%s），试下一个" % (port, e), flush=True)
    if srv is None:
        raise SystemExit("没有可用端口，请用 --port 指定一个")
    url = "http://%s:%d/" % (a.host, a.port)
    print("Pico HID 网页版上位机：%s  (Ctrl+C 退出)" % url, flush=True)
    print("协议：64B 报告 / 0xA5 / CRC8 / 无 Report ID", flush=True)
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _reset()
        srv.server_close()


if __name__ == "__main__":
    main()
