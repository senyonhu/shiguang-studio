"""Real embedded WebView2 network boundary probe against owned loopback endpoints.

The parent is this test's own Win32 window, not Ren'Py. This probes the browser
network boundary only, with CDP performing an actual click on a fixture button.
It never generates or commits a gameplay result and never modifies an install.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import math
import struct
import subprocess
import threading
import time
from uuid import uuid4
import wave


ROOT = Path(__file__).resolve().parents[1]
PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><title>Owned network boundary fixture</title><link rel="stylesheet" href="styles.css"></head>
<body><h1>Network boundary fixture</h1><img id="local-image" src="pixel.png"><button id="probe" disabled>Run controlled requests</button><pre id="report">waiting</pre>
<script src="probe.js"></script></body></html>'''
SCRIPT = r'''
const synthetic=location.search.includes('synthetic=1');
const report = {scope:'network only, no game result', local:null, requests:[], completed:false, syntheticResponse:!!document.querySelector('#synthetic-response')};
const output = document.querySelector('#report');
let sessionId=sessionStorage.getItem('security-session')||'';
function render(){output.textContent=JSON.stringify(report);}
function bounded(p){return Promise.race([p,new Promise(r=>setTimeout(()=>r({error:'timeout'}),4500))]);}
async function remote(kind){
  const answer = {kind};
  try {const r=await fetch('__BASE__/'+kind+'/fetch',{mode:'no-cors'});answer.fetch={type:r.type,status:r.status};}
  catch(e){answer.fetch={error:String(e)};}
  return answer;
}
async function worker(kind){
  if(kind==='shared') {
    const w=new SharedWorker('shared.js',{name:crypto.randomUUID()}); w.port.start();
    return bounded(new Promise(r=>{w.port.onmessage=e=>r(e.data);w.onerror=e=>r({kind,error:e.message});w.port.postMessage('__BASE__');}));
  }
  if(kind==='service') {
    const reg=await navigator.serviceWorker.register('service.js'); await navigator.serviceWorker.ready;
    return bounded(new Promise(r=>{const ch=new MessageChannel();ch.port1.onmessage=e=>r(e.data);(reg.active||reg.waiting||reg.installing).postMessage('__BASE__',[ch.port2]);}));
  }
  const w=kind==='blob'?new Worker(URL.createObjectURL(new Blob([__BLOB_CODE__],{type:'application/javascript'}))):new Worker('dedicated.js');
  const value=await bounded(new Promise(r=>{w.onmessage=e=>r(e.data);w.onerror=e=>r({kind,error:e.message});w.postMessage('__BASE__');}));w.terminate();return value;
}
window.chrome.webview.addEventListener('message',e=>{if(e.data.type==='init'){sessionId=e.data.session_id;sessionStorage.setItem('security-session',sessionId);document.querySelector('#probe').disabled=false;}if(__PRODUCTION__&&e.data.type==='start')document.querySelector('#probe').onclick();});
if(synthetic){document.querySelector('#probe').disabled=false;const s=document.createElement('span');s.id='reloaded';document.body.append(s);}
else window.chrome.webview.postMessage({protocol:'1.0',session_id:'',event_id:crypto.randomUUID(),type:'ready',payload:{}});
document.querySelector('#probe').onclick=async()=>{
  document.querySelector('#probe').disabled=true;
  try {report.local=await (await fetch('instructions.txt')).text();}catch(e){report.local={error:String(e)};}
  report.localResources={css:getComputedStyle(document.body).color,image:document.querySelector('#local-image').naturalWidth};
  try{const r=await fetch('instructions.txt',{headers:{Range:'bytes=2-5'}});report.localResources.range={status:r.status,body:await r.text(),header:r.headers.get('Content-Range')};}catch(e){report.localResources.range={error:String(e)};}
  try{const r=await fetch('tone.wav');const context=new AudioContext();const audio=await context.decodeAudioData(await r.arrayBuffer());report.localResources.audio={mime:r.headers.get('Content-Type'),duration:audio.duration};await context.close();}catch(e){report.localResources.audio={error:String(e)};}
  report.requests.push(await bounded(remote('document')));render();
  for(const kind of ['dedicated','shared','service','blob']){try{report.requests.push(await worker(kind));}catch(e){report.requests.push({kind,error:String(e)});}render();}
  report.requests.push(await bounded(new Promise(r=>{try{const w=new WebSocket('__WS__/websocket');w.onopen=()=>{r({kind:'websocket',opened:true});w.close();};w.onerror=()=>r({kind:'websocket',opened:false});}catch(e){r({kind:'websocket',opened:false,error:String(e)});}})));render();
  report.webrtc={available:typeof RTCPeerConnection==='function'};
  if(report.webrtc.available){try{const p=new RTCPeerConnection({iceServers:[{urls:'__STUN__'}]});p.createDataChannel('owned-network-probe');await p.setLocalDescription(await p.createOffer());await new Promise(r=>setTimeout(r,1400));p.close();report.webrtc.tested=true;}catch(e){report.webrtc.error=String(e);}}
  report.completed=true;render();output.id='done';
  if(__PRODUCTION__&&(!__SW_RELOAD__||synthetic))window.chrome.webview.postMessage({protocol:'1.0',session_id:sessionId,event_id:crypto.randomUUID(),type:'error',payload:{code:'SECURITY_PROBE_REPORT',message:JSON.stringify(report)}});
  if(__SW_RELOAD__&&!synthetic)location.href='?synthetic=1';
};
if(__PRODUCTION__&&synthetic)setTimeout(()=>document.querySelector('#probe').onclick(),0);
'''
WORKER = r'''
async function run(base,kind) {
 const result={kind};
 try{result.local=await(await fetch(new URL('instructions.txt',self.location.origin+'/'))).text();}catch(e){result.local={error:String(e)};}
 try{const r=await fetch(base+'/'+kind+'/fetch',{mode:'no-cors'});result.fetch={type:r.type,status:r.status};}catch(e){result.fetch={error:String(e)};}
 try{importScripts(base+'/'+kind+'/import.js');result.imported=Boolean(self.controlledImport);}catch(e){result.imported=false;result.import_error=String(e);}
 result.websocket=await Promise.race([new Promise(r=>{try{const w=new WebSocket(base.replace('http:','ws:')+'/'+kind+'/websocket');w.onopen=()=>{r({opened:true});w.close();};w.onerror=()=>r({opened:false});}catch(e){r({opened:false,error:String(e)});}}),new Promise(r=>setTimeout(()=>r({error:'timeout'}),3000))]);
 return result;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class Endpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.records.append({'path': self.path, 'at': time.time(),
                                    'origin': self.headers.get('Origin'), 'upgrade': self.headers.get('Upgrade')})
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            value = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(value.encode()).digest()).decode())
            self.end_headers()
            time.sleep(0.4)
            self.close_connection = True
            return
        data = b'self.controlledImport=true;' if self.path.endswith('.js') else b'owned endpoint reached'
        self.send_response(200)
        self.send_header('Content-Type', 'application/javascript' if self.path.endswith('.js') else 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


def create_parent():
    """Create only the fixture process's own visible top-level window."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.SetProcessDPIAware()
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.DefWindowProcW.restype = ctypes.c_ssize_t

    @callback_type
    def callback(hwnd, message, wparam, lparam):
        return user.DefWindowProcW(hwnd, message, wparam, lparam)

    class WindowClass(ctypes.Structure):
        _fields_ = [('style', wintypes.UINT), ('callback', callback_type), ('class_extra', ctypes.c_int),
                    ('window_extra', ctypes.c_int), ('instance', wintypes.HINSTANCE), ('icon', wintypes.HICON),
                    ('cursor', wintypes.HANDLE), ('background', wintypes.HBRUSH),
                    ('menu', wintypes.LPCWSTR), ('name', wintypes.LPCWSTR)]

    name = 'RenPyBridgeOwnedSecurityFixture_' + uuid4().hex
    instance = kernel.GetModuleHandleW(None)
    cls = WindowClass(0, callback, 0, 0, instance, None, None, None, None, name)
    user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
    if not user.RegisterClassW(ctypes.byref(cls)):
        raise ctypes.WinError(ctypes.get_last_error())
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    hwnd = user.CreateWindowExW(0, name, 'Owned WebView2 network security fixture', 0x10CF0000,
                                160, 100, 1280, 720, None, None, instance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user.DispatchMessageW.restype = ctypes.c_ssize_t
    user.DestroyWindow.argtypes = [wintypes.HWND]
    # Keep callback alive until DestroyWindow.
    return hwnd, user, callback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, default=ROOT / 'runtimes/html-minigame-embedded-host/win-x64/HtmlMinigameEmbeddedHost.exe')
    parser.add_argument('--browser', type=Path, default=ROOT / 'runtimes/webview2-fixed-154.0.4258.53/Microsoft.WebView2.FixedVersionRuntime.154.0.4258.53.x64')
    parser.add_argument('--port', type=int, default=9391)
    parser.add_argument('--expect', choices=['blocked', 'baseline'], default='blocked')
    parser.add_argument('--service-worker-reload', action='store_true', help='Also try to strip CSP using a synthesized service-worker navigation response')
    parser.add_argument('--production', action='store_true', help='No development flag or CDP port; security fixture runs on start and reports observations over IPC')
    args = parser.parse_args()
    if os.name != 'nt' or not 1024 <= args.port <= 65535:
        raise ValueError('A Windows host and a development loopback port are required')
    run = ROOT / 'evidence/html-minigames/network-boundary/runs' / str(time.time_ns())
    run.mkdir(parents=True, exist_ok=False)
    package = run / 'fixture-package'
    package.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    server.records = []
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(('127.0.0.1', 0))
    udp.settimeout(0.2)
    udp_records = []
    udp_stop = threading.Event()
    def udp_reader():
        while not udp_stop.is_set():
            try:
                data, address = udp.recvfrom(4096)
                udp_records.append({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'at':time.time(),'source':address})
            except socket.timeout:
                continue
            except OSError:
                break
    threading.Thread(target=udp_reader, daemon=True).start()
    endpoint = 'http://127.0.0.1:' + str(server.server_port)
    blob_script = WORKER + "onmessage=async e=>postMessage(await run(e.data,'blob'));"
    script = SCRIPT.replace('__BASE__',endpoint).replace('__WS__',endpoint.replace('http:','ws:')).replace('__STUN__','stun:127.0.0.1:'+str(udp.getsockname()[1])).replace('__BLOB_CODE__',json.dumps(blob_script)).replace('__SW_RELOAD__',str(args.service_worker_reload).lower()).replace('__PRODUCTION__',str(args.production).lower())
    scripts = {'index.html': PAGE, 'probe.js': script,
        'dedicated.js': WORKER + "onmessage=async e=>postMessage(await run(e.data,'dedicated'));",
        'shared.js': WORKER + "onconnect=e=>{const p=e.ports[0];p.onmessage=async e=>p.postMessage(await run(e.data,'shared'));p.start();};",
        'service.js': WORKER + "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('message',e=>e.waitUntil(run(e.data,'service').then(r=>e.ports[0].postMessage(r))));"
            + "self.addEventListener('fetch',e=>{if(e.request.mode==='navigate'&&e.request.url.includes('synthetic=1'))e.respondWith(Promise.resolve(new Response("
            + json.dumps(PAGE.replace('<body>','<body><h2 id=synthetic-response>Service worker synthesized this response without CSP</h2>')) + ",{headers:{'Content-Type':'text/html'}})));});",
        'instructions.txt': 'local resource loaded', 'styles.css':'body{color:rgb(37,45,61)}'}
    for name, content in scripts.items():
        (package / name).write_text(content, encoding='utf-8')
    (package/'pixel.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII='))
    with wave.open(str(package/'tone.wav'),'wb') as tone:
        tone.setparams((1,2,8000,1600,'NONE','not compressed'))
        tone.writeframes(b''.join(struct.pack('<h',int(2500*math.sin(i*2*math.pi*440/8000))) for i in range(1600)))
    payload_names = [*scripts,'pixel.png','tone.wav']
    manifest = {'format_version': '1.0', 'prototype': True, 'game_id': 'security_fixture', 'version': '1.0.0',
                'entry': 'index.html', 'protocol': '1.0', 'inputs': {}, 'results': {'outcomes': ['done'], 'fields': {}},
                'save_capabilities': {'entry_restart': True, 'checkpoint': False, 'snapshot_version': None},
                'files': [{'path': name, 'sha256': sha(package/name), 'bytes': (package/name).stat().st_size} for name in sorted(payload_names)]}
    write_json(package/'manifest.json', manifest)
    digest = hashlib.sha256(b''.join(name.encode()+b'\0'+sha(package/name).encode()+b'\n'
        for name in sorted([*payload_names, 'manifest.json'], key=lambda item: item.encode()))).hexdigest()
    session = uuid4().hex
    hwnd, user, callback = create_parent()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('WEBVIEW2_')}
    env['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'] = str(args.browser.resolve(strict=True))
    command = [str(args.host.resolve(strict=True)), '--package-root', str(package), '--entry', 'index.html',
        '--game-id', 'security_fixture', '--package-version', '1.0.0', '--package-hash', digest, '--session-id', session,
        '--user-data-root', str(run/'browser-profile'), '--parent-window-handle', str(hwnd), '--parent-process-id', str(os.getpid())]
    if not args.production:
        command.extend(['--development','--devtools-port',str(args.port)])
    report = {'scope': 'real WebView2 network boundary only; owned Win32 test parent, not an SDL integration test',
        'run': str(run), 'status': 'failed', 'host_sha256': sha(args.host), 'dll_sha256': sha(args.host.parent/'HtmlMinigameEmbeddedHost.dll'),
        'build_report_sha256': sha(args.host.parent/'build-report.json'), 'package_digest': digest, 'endpoint': endpoint,
        'parent_pid': os.getpid(), 'parent_hwnd': hwnd, 'command': command, 'expect': args.expect, 'production':args.production}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=(run/'host-stderr.log').open('w', encoding='utf-8'), text=True, encoding='utf-8', env=env)
    messages = []
    ready = threading.Event()
    finished = threading.Event()
    ipc_report = []
    def reader():
        for line in process.stdout:
            value = json.loads(line)
            messages.append(value)
            if value.get('type')=='error' and value.get('payload',{}).get('code')=='SECURITY_PROBE_REPORT':
                ipc_report.append(json.loads(value['payload']['message']))
                finished.set()
            if value.get('type') == 'ready':
                ready.set()
                for kind, payload in [('init', {'game_id':'security_fixture','package_version':'1.0.0','package_hash':digest,'inputs':{},'settings':{'audio_volume':0}}),('start',{})]:
                    process.stdin.write(json.dumps({'protocol':'1.0','session_id':session,'event_id':uuid4().hex,'type':kind,'payload':payload})+'\n')
                    process.stdin.flush()
    threading.Thread(target=reader, daemon=True).start()
    actions = [{'type':'click','selector':'#probe'}, {'type':'wait_selector','selector':'#done','timeout':45},
               {'type':'assert_text','selector':'#done','text':'"completed":true'}, {'type':'capture','name':'network_results'}]
    if args.service_worker_reload:
        actions = [{'type':'click','selector':'#probe'},{'type':'wait_selector','selector':'#reloaded','timeout':45}]+actions
    write_json(run/'actions.json', actions)
    node = None if args.production else subprocess.Popen(['node',str(ROOT/'tools/probe_html_cdp.mjs'),'--port',str(args.port),'--output',str(run/'cdp'),
        '--url-prefix','https://minigame-security_fixture.invalid/','--actions',str(run/'actions.json')],
        stdout=(run/'cdp-stdout.log').open('w', encoding='utf-8'),stderr=(run/'cdp-stderr.log').open('w',encoding='utf-8'))
    began = time.monotonic()
    try:
        while ((not finished.is_set()) if args.production else node.poll() is None) and time.monotonic()-began < 100:
            msg = wintypes.MSG()
            while user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.015)
        if (args.production and not finished.is_set()) or (not args.production and node.poll() is None):
            raise TimeoutError('CDP fixture timed out')
        if args.production:
            actual=ipc_report[-1]
        else:
            cdp = json.loads((run/'cdp/report.json').read_text(encoding='utf-8'))
            if node.returncode or cdp['status'] != 'passed':
                raise RuntimeError('Actual fixture interaction failed; see CDP report')
            actual = json.loads(next(action['actual'] for action in cdp['actions'] if action['type']=='assert_text'))
        report.update(actual=actual, endpoint_requests=list(server.records), udp_requests=list(udp_records), host_messages=messages, host_pid=process.pid)
        kinds = {value.get('kind') for value in actual['requests']}
        resources = actual['localResources']
        if actual['local'] != 'local resource loaded' or kinds != {'document','dedicated','shared','service','blob','websocket'}:
            raise AssertionError('Local resources or all declared request sources were not tested')
        if (resources['css']!='rgb(37, 45, 61)' or resources['image']!=1 or resources['range']['status']!=206
                or resources['range']['body']!='cal ' or resources['audio'].get('mime')!='audio/wav'
                or abs(resources['audio'].get('duration',0)-0.2)>0.001):
            raise AssertionError('Local CSS/image/audio or HTTP byte-range support failed')
        if any(value.get('local')!='local resource loaded' for value in actual['requests'] if value['kind'] in {'dedicated','shared','service','blob'}):
            raise AssertionError('A real worker did not load its local resource')
        if args.expect == 'blocked' and (server.records or udp_records or actual.get('webrtc',{}).get('available')):
            raise AssertionError('Network boundary bypass: a controlled endpoint was reached')
        if args.expect=='blocked' and args.service_worker_reload and actual['syntheticResponse']:
            raise AssertionError('A service worker replaced the mandatory document response policy')
        report['status'] = 'passed'
    except Exception as error:
        report.update(error={'type':type(error).__name__,'message':str(error)}, endpoint_requests=list(server.records), udp_requests=list(udp_records), host_messages=messages)
    finally:
        if node is not None and node.poll() is None:
            node.terminate();node.wait(timeout=10)
        if process.poll() is None:
            try:
                process.stdin.write(json.dumps({'protocol':'1.0','session_id':session,'event_id':uuid4().hex,'type':'cancel','payload':{'reason':'security_probe_finished'}})+'\n')
                process.stdin.flush()
                deadline=time.monotonic()+5
                while process.poll() is None and time.monotonic()<deadline:
                    msg=wintypes.MSG()
                    while user.PeekMessageW(ctypes.byref(msg),None,0,0,1):
                        user.TranslateMessage(ctypes.byref(msg));user.DispatchMessageW(ctypes.byref(msg))
                    time.sleep(0.02)
                if process.poll() is None:process.terminate()
                process.wait(timeout=10)
            except (OSError,subprocess.TimeoutExpired):
                process.kill();process.wait(timeout=10)
        report['host_exit_code']=process.returncode
        user.DestroyWindow(hwnd)
        server.shutdown();server.server_close()
        udp_stop.set();udp.close()
        report['finished_at']=time.time()
        write_json(run/'report.json',report)
    print(json.dumps({'status':report['status'],'report':str(run/'report.json'),'endpoint_requests':len(server.records)}))
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
