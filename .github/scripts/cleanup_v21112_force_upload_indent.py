from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")
old_api = '''        if not committed:\n                return {"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚"}, 502\n'''
new_api = '''        if not committed:\n            return {"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚"}, 502\n'''
old_chat = '''        if not committed:\n                await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))\n                return\n'''
new_chat = '''        if not committed:\n            await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))\n            return\n'''
assert source.count(old_api) == 1
assert source.count(old_chat) == 1
source = source.replace(old_api, new_api, 1).replace(old_chat, new_chat, 1)
path.write_text(source, encoding="utf-8")
