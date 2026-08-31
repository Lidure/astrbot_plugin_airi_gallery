from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

old_put = '''                body: dict = {
                    "message": message,
                    "content": content_b64,
                }
'''
new_put = '''                body: dict = {
                    "message": message,
                    "content": content_b64,
                    "branch": branch,
                }
'''
old_delete = '''                body = {"message": message, "sha": sha}
'''
new_delete = '''                body = {"message": message, "sha": sha, "branch": branch}
'''

if text.count(old_put) != 1:
    raise SystemExit(f"expected exactly one Gitee put body, found {text.count(old_put)}")
if text.count(old_delete) != 1:
    raise SystemExit(f"expected exactly one Gitee delete body, found {text.count(old_delete)}")

text = text.replace(old_put, new_put, 1)
text = text.replace(old_delete, new_delete, 1)
path.write_text(text, encoding="utf-8")
