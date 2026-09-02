from pathlib import Path

path = Path("tools/tmp_apply_upload_hot_path_green.py")
text = path.read_text(encoding="utf-8")
old = '''    "gallery_store.py",\n    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        if save:\\n            self.save_hash_index()\\n\\n    def forget_file_hash(\\n''' + "'''" + ''',\n'''
new = '''    "gallery_store.py",\n    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        if save:\\n            self.save_hash_index()\\n''' + "'''" + ''',\n'''
if old not in text:
    raise SystemExit("original broken anchor not found")
text = text.replace(old, new, 1)
# The replacement payload still needs to insert max-index cache tracking.
needle = '''    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        if save:\\n            self.save_hash_index()\\n''' + "'''" + ''',\n    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        self._remember_numeric_index(local_path)\\n        if save:\\n            self.save_hash_index()\\n\\n    def forget_file_hash(\\n''' + "'''"
replacement = '''    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        if save:\\n            self.save_hash_index()\\n''' + "'''" + ''',\n    ''' + "'''" + '''            if self.hash_index.get(git_path) != entry:\\n                self.hash_index[git_path] = entry\\n                self.hash_index_dirty = True\\n        self._remember_numeric_index(local_path)\\n        if save:\\n            self.save_hash_index()\\n''' + "'''"
if needle not in text:
    raise SystemExit("replacement block not found")
text = text.replace(needle, replacement, 1)
path.write_text(text, encoding="utf-8")
print("migration anchor corrected")
