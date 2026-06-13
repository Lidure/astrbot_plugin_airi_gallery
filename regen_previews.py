from pathlib import Path
import importlib.util
import asyncio

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('main_mod', str(root / 'main.py'))
main_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_mod)

class DummyCtx: pass
app = main_mod.Main(DummyCtx())

cats = ['表情包', '日常', '搞笑', '猫猫', '风景', '其它']

async def run_build():
    path = await app._build_category_list_image(cats)
    print(path)
    help_path = await app._build_help_image()
    print(help_path)

asyncio.get_event_loop().run_until_complete(run_build())
