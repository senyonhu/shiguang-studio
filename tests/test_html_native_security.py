"""Targeted regression checks for real native stream behavior and probe endpoints."""
from __future__ import annotations

import base64
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('html_network_probe', ROOT/'tools/probe_html_network.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def endpoint():
    server = ThreadingHTTPServer(('127.0.0.1', 0), probe.Endpoint)
    server.records = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_owned_endpoint_observes_real_http_script_request(endpoint):
    connection = HTTPConnection('127.0.0.1', endpoint.server_port, timeout=3)
    connection.request('GET', '/shared/import.js')
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader('Content-Type') == 'application/javascript'
    assert response.read() == b'self.controlledImport=true;'
    connection.close()
    assert [row['path'] for row in endpoint.records] == ['/shared/import.js']


def test_owned_endpoint_observes_valid_websocket_upgrade(endpoint):
    key = base64.b64encode(b'controlled-key12').decode()
    with socket.create_connection(('127.0.0.1', endpoint.server_port), timeout=3) as client:
        client.sendall(('GET /worker/websocket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\n'
            'Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+key+'\r\n\r\n').encode())
        data = b''
        while b'\r\n\r\n' not in data:
            data += client.recv(4096)
    expected = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
    assert data.startswith(b'HTTP/1.0 101 ')
    assert b'Sec-WebSocket-Accept: '+expected+b'\r\n' in data
    assert endpoint.records[-1]['path'] == '/worker/websocket'
    assert endpoint.records[-1]['upgrade'] == 'websocket'


@pytest.mark.skipif(os.name != 'nt' or not (probe.ROOT/'runtimes/html-host-dependencies/dotnet-sdk-10.0.401/dotnet.exe').is_file(),
                    reason='real native DLL stream regression needs the pinned Windows .NET SDK')
def test_actual_compiled_host_range_stream_bounds():
    result = subprocess.run([sys.executable, str(ROOT/'tools/probe_html_resource_slice.py')],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=70)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    report = json.loads(Path(receipt['report']).read_text(encoding='utf-8'))
    assert report['status'] == 'passed'
    assert len(report['checks']) == 9
    assert report['host_dll_sha256'] == hashlib.sha256((ROOT/'runtimes/html-minigame-embedded-host/win-x64/HtmlMinigameEmbeddedHost.dll').read_bytes()).hexdigest()
