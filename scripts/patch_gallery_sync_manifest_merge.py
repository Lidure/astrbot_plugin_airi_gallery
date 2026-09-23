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

empty_manifest_expr = '''json.dumps(\n        {\n            "version": 1,\n            "algorithm": MANIFEST_ALGORITHM,\n            "max_index": 0,\n            "files": {},\n        }\n    ).encode("utf-8")'''

transaction_test = Path('tests/test_gallery_sync_upload_transaction.py')
transaction_source = transaction_test.read_text(encoding='utf-8')
transaction_marker = '''    sync.set_sync_enabled(enabled)\n    sync.remote_manifest_reader = Mock(return_value=(True, {}))\n'''
transaction_replacement = f'''    sync.set_sync_enabled(enabled)\n    remote.get_file = Mock(return_value={empty_manifest_expr})\n    sync.remote_manifest_reader = Mock(return_value=(True, {{}}))\n'''
if 'remote.get_file = Mock(return_value=json.dumps(' not in transaction_source:
    if transaction_marker not in transaction_source:
        raise SystemExit('transaction test fixture marker not found')
    transaction_source = transaction_source.replace(
        transaction_marker, transaction_replacement, 1
    )
transaction_test.write_text(transaction_source, encoding='utf-8')

performance_test = Path('tests/test_upload_hot_path_performance.py')
performance_source = performance_test.read_text(encoding='utf-8')
performance_marker = '''    sync.set_sync_enabled(True)\n    return sync, store, remote\n'''
performance_replacement = f'''    sync.set_sync_enabled(True)\n    remote.get_file = Mock(return_value={empty_manifest_expr})\n    return sync, store, remote\n'''
if 'remote.get_file = Mock(return_value=json.dumps(' not in performance_source.split('def test_category_local_index', 1)[0]:
    if performance_marker not in performance_source:
        raise SystemExit('performance test fixture marker not found')
    performance_source = performance_source.replace(
        performance_marker, performance_replacement, 1
    )
performance_test.write_text(performance_source, encoding='utf-8')
