"""Airi Gallery 代理服务器
运行: python upload_server.py
"""
import http.server
import json
import os
import time
import urllib.request
from pathlib import Path

ASTRBOT_URL = "http://localhost:6185"
PLUGIN_NAME = "astrbot_plugin_airi_gallery"
API_PREFIX = f"{ASTRBOT_URL}/api/plug/{PLUGIN_NAME}"
GALLERY_DIR = Path(__file__).resolve().parent / "pages" / "gallery"
JWT = {"token": None, "expires": 0}


def get_jwt():
    if JWT["token"] and time.time() < JWT["expires"]:
        return JWT["token"]
    try:
        data = json.dumps({"username": "小浅子", "password": "Ctx2003923"}).encode()
        req = urllib.request.Request(
            f"{ASTRBOT_URL}/api/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            if resp.status == 200 and result.get("data", {}).get("token"):
                JWT["token"] = result["data"]["token"]
                JWT["expires"] = time.time() + 3500
                return JWT["token"]
    except Exception as e:
        print(f"登录失败: {e}")
    return None


def proxy_request(method, path, body=None):
    token = get_jwt()
    if not token:
        raise Exception("无法连接 AstrBot")
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{API_PREFIX}{path}"
    if body:
        headers["Content-Type"] = "application/json"
        data = body.encode()
    else:
        data = None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(), resp.status


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            api_path = self.path[4:]
            try:
                body, status = proxy_request("GET", api_path)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            req_path = self.path.split("?")[0].lstrip("/") or "index.html"
            file_path = GALLERY_DIR / req_path
            if file_path.is_file():
                self.send_response(200)
                ct = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript"}.get(file_path.suffix, "application/octet-stream")
                self.send_header("Content-Type", ct)
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        if self.path.startswith("/api/"):
            api_path = self.path[4:]
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length else None
            try:
                body, status = proxy_request("POST", api_path, body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", 8080), Handler)
    print("=" * 50)
    print("Airi Gallery 代理服务器")
    print(f"页面目录: {GALLERY_DIR}")
    print(f"访问地址: http://localhost:8080")
    print(f"Tunnel 地址: https://gallery.lidure22.xyz")
    print("=" * 50)
    server.serve_forever()
