from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")
old = '''    async def _handle_upload(self, event: AstrMessageEvent, category: str):
        category_dir = self._resolve_existing_category_dir(category)
'''
new = '''    async def _handle_upload(self, event: AstrMessageEvent, category: str):
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        category_dir = self._resolve_existing_category_dir(category)
'''
if old not in source:
    raise SystemExit("expected _handle_upload anchor not found")
if new in source:
    raise SystemExit("permission guard already present")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
