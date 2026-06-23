"""Airi Gallery 公开代理服务器
将 gallery 页面和 API 通过代理暴露，自动处理 AstrBot 认证。
运行: python upload_server.py
Tunnel 指向 localhost:8080
"""
import asyncio
import json
import os
import time
from pathlib import Path

from quart import Quart, request, jsonify, send_file
import aiohttp

app = Quart(__name__)

ASTRBOT_URL = os.environ.get("ASTRBOT_URL", "http://localhost:6185")
ASTRBOT_USER = os.environ.get("ASTRBOT_USER", "admin")
ASTRBOT_PASS = os.environ.get("ASTRBOT_PASS", "admin")
PLUGIN_NAME = "astrbot_plugin_airi_gallery"
API_BASE = f"{ASTRBOT_URL}/api/plug/{PLUGIN_NAME}"
PAGE_DIR = Path(__file__).resolve().parent / "pages" / "gallery"

_jwt_cache = {"token": None, "expires": 0}


async def _get_jwt():
    if _jwt_cache["token"] and time.time() < _jwt_cache["expires"]:
        return _jwt_cache["token"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ASTRBOT_URL}/api/auth/login",
                json={"username": ASTRBOT_USER, "password": ASTRBOT_PASS},
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("data", {}).get("token"):
                    token = data["data"]["token"]
                    _jwt_cache["token"] = token
                    _jwt_cache["expires"] = time.time() + 3500
                    return token
    except Exception as e:
        print(f"[proxy] 登录失败: {e}")
    return None


async def _proxy_get(path, params=None):
    token = await _get_jwt()
    if not token:
        return {"error": "无法连接 AstrBot"}
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}{path}", params=params, headers=headers) as resp:
            return await resp.json()


async def _proxy_post(path, data):
    token = await _get_jwt()
    if not token:
        return {"error": "无法连接 AstrBot"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE}{path}", json=data, headers=headers) as resp:
            return await resp.json()


@app.route("/")
async def index():
    index_file = PAGE_DIR / "index.html"
    if index_file.exists():
        return await send_file(str(index_file))
    return "Gallery page not found", 404


@app.route("/<path:filename>")
async def static_files(filename):
    file_path = PAGE_DIR / filename
    if file_path.exists() and file_path.is_file():
        return await send_file(str(file_path))
    return "Not found", 404


@app.route("/api/categories")
async def api_categories():
    return jsonify(await _proxy_get("/pub/categories", {"token": "public"}))


@app.route("/api/category_images", methods=["GET"])
async def api_category_images():
    category = request.args.get("category", "")
    return jsonify(await _proxy_get("/category_images", {"category": category}))


@app.route("/api/category_image", methods=["GET"])
async def api_category_image():
    category = request.args.get("category", "")
    name = request.args.get("name", "")
    return jsonify(await _proxy_get("/category_image", {"category": category, "name": name}))


@app.route("/api/upload", methods=["POST"])
async def api_upload():
    data = await request.get_json()
    return jsonify(await _proxy_post("/pub/upload", data))


@app.route("/api/delete_image", methods=["POST"])
async def api_delete_image():
    data = await request.get_json()
    return jsonify(await _proxy_post("/delete_image", data))


if __name__ == "__main__":
    print("=" * 50)
    print("Airi Gallery 代理服务器")
    print(f"AstrBot: {ASTRBOT_URL}")
    print(f"代理地址: http://localhost:8080")
    print(f"请将 Tunnel 指向 localhost:8080")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8080)
