from pathlib import Path

path = Path(__file__).resolve().parents[1] / "main.py"
source = path.read_text(encoding="utf-8")
old = '''                    await self._send_upload_decision_hint(\n                event, decision, pending_image_bytes=image_bytes, pending_name=pending_name\n            )'''
new = '''                    await self._send_upload_decision_hint(\n                        event,\n                        decision,\n                        pending_image_bytes=image_bytes,\n                        pending_name=pending_name,\n                    )'''
count = source.count(old)
assert count == 2, count
path.write_text(source.replace(old, new), encoding="utf-8")
