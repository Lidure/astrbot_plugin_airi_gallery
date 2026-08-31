from pathlib import Path

main_path = Path("main.py")
source = main_path.read_text(encoding="utf-8")
old_check = '''    def _check_upload_token(self, token: str) -> bool:
        expected = str(self.config.get("upload_token", "")).strip()
        if not expected:
            return True
        return token == expected
'''
new_check = '''    def _check_upload_token(self, token: str) -> bool:
        expected = str(self.config.get("upload_token", "")).strip()
        if not expected:
            return False
        return secrets.compare_digest(str(token), expected)
'''
if old_check not in source:
    raise SystemExit("expected upload token checker anchor not found")
source = source.replace(old_check, new_check, 1)

old_api = '''        try:
            data = await request.get_json()
            token = str(data.get("token", ""))
            if not self._check_upload_token(token):
'''
new_api = '''        try:
            data = await request.get_json()
            expected_token = str(self.config.get("upload_token", "")).strip()
            if not expected_token:
                return jsonify({"ok": False, "error": "公开上传未启用"}), 403
            token = str(data.get("token", ""))
            if not self._check_upload_token(token):
'''
if old_api not in source:
    raise SystemExit("expected public upload auth anchor not found")
source = source.replace(old_api, new_api, 1)
main_path.write_text(source, encoding="utf-8")

schema_path = Path("_conf_schema.json")
schema = schema_path.read_text(encoding="utf-8")
old_hint = '"hint": "用于外部上传页面的访问密钥。留空则无需密钥（不安全）。建议设置以防止陌生人上传。"'
new_hint = '"hint": "用于外部上传页面的访问密钥。留空将关闭公开上传接口；公开写入必须设置密钥。"'
if old_hint not in schema:
    raise SystemExit("expected upload_token hint anchor not found")
schema_path.write_text(schema.replace(old_hint, new_hint, 1), encoding="utf-8")
