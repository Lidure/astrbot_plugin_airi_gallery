from pathlib import Path

path = Path('gallery_sync.py')
source = path.read_text(encoding='utf-8')

import_marker = '\n\n_UNCERTAIN_DELETE_STATUSES = {0, 500, 502, 503, 504}\n'
import_block = '''\n\ntry:\n    from .gallery_manifest import merge_remote_category_manifest\nexcept ImportError:\n    from gallery_manifest import merge_remote_category_manifest\n'''
if 'from .gallery_manifest import merge_remote_category_manifest' not in source:
    if import_marker not in source:
        raise SystemExit('import marker not found')
    source = source.replace(import_marker, import_block + import_marker, 1)

old = '''            try:\n                manifest_payload = json.dumps(\n                    self.manifest_payload_factory(category),\n                    ensure_ascii=False,\n                    separators=(",", ":"),\n                    sort_keys=True,\n                ).encode("utf-8")\n            except Exception as exc:\n                self._warning(f"[Git Sync] 生成上传感知索引失败: {exc}")\n                self._rollback_staged_uploads(staged_paths, category)\n                return False\n'''
new = '''            try:\n                remote_manifest_raw = self.remote.get_file(self.manifest_path)\n                if remote_manifest_raw is None:\n                    raise ValueError("无法读取远端全局感知索引")\n                remote_manifest_payload = json.loads(\n                    remote_manifest_raw.decode("utf-8")\n                )\n                category_manifest_payload = self.manifest_payload_factory(category)\n                merged_manifest_payload = merge_remote_category_manifest(\n                    remote_manifest_payload,\n                    category_manifest_payload,\n                    category=category,\n                    algorithm=self.manifest_algorithm,\n                )\n                manifest_payload = json.dumps(\n                    merged_manifest_payload,\n                    ensure_ascii=False,\n                    separators=(",", ":"),\n                    sort_keys=True,\n                ).encode("utf-8")\n            except Exception as exc:\n                self._warning(f"[Git Sync] 合并上传感知索引失败: {exc}")\n                self._rollback_staged_uploads(staged_paths, category)\n                return False\n'''
if 'merged_manifest_payload = merge_remote_category_manifest(' not in source:
    if old not in source:
        raise SystemExit('manifest generation block not found')
    source = source.replace(old, new, 1)

path.write_text(source, encoding='utf-8')
