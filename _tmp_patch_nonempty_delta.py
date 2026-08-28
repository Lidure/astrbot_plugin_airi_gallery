from pathlib import Path

safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
old_safety = '''    deletes: list[dict[str, object]] = []\n    upserts: list[dict[str, object]] = []\n    for name in sorted(set(original) | set(final)):\n        before = original.get(name)\n        after = final.get(name)\n        if before == after:\n            continue\n        if before is not None:\n            deletes.append(\n                {\n                    "path": name,\n                    "mode": before["mode"],\n                    "type": before["type"],\n                    "sha": None,\n                }\n            )\n        if after is not None:\n            upserts.append(dict(after))\n    return tuple(deletes), tuple(upserts)\n'''
new_safety = '''    deletes: list[dict[str, object]] = []\n    upserts: list[dict[str, object]] = []\n    for name in sorted(set(original) | set(final)):\n        before = original.get(name)\n        after = final.get(name)\n        if before == after:\n            continue\n        # Replacing an existing path only needs an upsert. Deleting it first can\n        # transiently empty a category tree, which GitHub rejects with HTTP 404.\n        if before is not None and after is None:\n            deletes.append(\n                {\n                    "path": name,\n                    "mode": before["mode"],\n                    "type": before["type"],\n                    "sha": None,\n                }\n            )\n        if after is not None:\n            upserts.append(dict(after))\n    return tuple(deletes), tuple(upserts)\n'''
if old_safety not in safety:
    raise SystemExit("gallery_safety delta block not found")
safety_path.write_text(safety.replace(old_safety, new_safety, 1), encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
old_main = '''        """在现有分类 tree 上分块删除旧路径，再分块写入最终路径。"""\n        current_tree_sha = base_tree_sha\n        phase_name = "delete"\n        for entries in (deletes, upserts):\n            if entries is upserts:\n                phase_name = "upsert"\n'''
new_main = '''        """在现有分类 tree 上先写入最终路径，再分块删除真正废弃的旧路径。"""\n        current_tree_sha = base_tree_sha\n        phase_name = "upsert"\n        for entries in (upserts, deletes):\n            if entries is deletes:\n                phase_name = "delete"\n'''
if old_main not in main:
    raise SystemExit("main delta order block not found")
main_path.write_text(main.replace(old_main, new_main, 1), encoding="utf-8")
