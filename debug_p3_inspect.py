from pathlib import Path
from PIL import Image as PILImage, ImageFont, ImageDraw

root = Path(r"f:\NORMAL\My_bot\astrbot_plugin_airi_gallery")
p2 = root / 'p2.png'
p3 = root / 'p3.png'
print('p2 exists:', p2.exists())
print('p3 exists:', p3.exists())
try:
    if p2.exists():
        with PILImage.open(p2) as im:
            w, h = im.size
            print('p2 size:', w, h)
    else:
        print('p2 missing')
except Exception as e:
    print('p2 open error', e)

try:
    if p3.exists():
        with PILImage.open(p3) as im:
            print('p3 original size:', im.size)
            img = im.convert('RGBA')
            # emulate updated main.py logic (自适应可用宽度并尽量不重叠)
            try:
                if p2.exists():
                    with PILImage.open(p2) as p2test:
                        p2w, p2h = p2test.convert('RGBA').size
                else:
                    p2w, p2h = (0, 280)
            except Exception:
                p2w, p2h = (0, 280)
            print('p2 for logic size:', p2w, p2h)
            max_h = int(p2h or 280)
            print('max_h used for thumbnail:', max_h)
            img_thumb = img.copy()
            img_thumb.thumbnail((max_h, max_h), PILImage.Resampling.LANCZOS)
            print('p3 after thumbnail:', img_thumb.size)
            desired_w = int(img_thumb.width * 2)
            desired_h = int(img_thumb.height * 2)
            print('desired double size:', desired_w, desired_h)
            # simulate title width
            try:
                font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 54)
            except Exception:
                font = ImageFont.load_default()
            d = ImageDraw.Draw(PILImage.new('RGBA', (10, 10)))
            title_w = d.textbbox((0, 0), '分类列表', font=font)[2]
            print('title_w:', title_w)
            padding_x = 42
            p3_x = padding_x + title_w + 16
            print('initial p3_x:', p3_x)
            canvas_width = padding_x * 2 + 3 * 284 + (3 - 1) * 18
            print('canvas width:', canvas_width)
            right_limit = canvas_width - (p2w + 22) if p2w else canvas_width - 22
            print('right_limit:', right_limit)
            move_offset = 40
            space_no_move = right_limit - p3_x - 8
            space_after_move = right_limit - (p3_x + move_offset) - 8
            print('space_no_move:', space_no_move, 'space_after_move:', space_after_move)

            if space_after_move >= desired_w:
                target_w = desired_w
                final_x = p3_x + move_offset
                print('use desired and move')
            elif space_no_move >= desired_w:
                target_w = desired_w
                final_x = p3_x
                print('use desired without move')
            else:
                candidate = max(space_after_move, space_no_move)
                if candidate < 40:
                    target_w = 0
                    print('not enough space for p3 (skip)')
                else:
                    target_w = min(desired_w, max(40, candidate))
                    print('shrink to fit target_w:', target_w)
                final_x = min(p3_x + move_offset, max(0, right_limit - int(target_w) - 8))

            if target_w and img_thumb.width:
                target_h = max(1, int(target_w * (img_thumb.height / img_thumb.width)))
                print('final target size:', int(target_w), target_h)
                print('final_x:', final_x)
            else:
                print('p3 skipped')
    else:
        print('p3 missing — main.py 跳过 p3 合成')
except Exception as e:
    print('p3 processing error', e)
