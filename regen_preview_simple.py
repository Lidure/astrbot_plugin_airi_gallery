from pathlib import Path
import math
import time
from PIL import Image as PILImage, ImageDraw, ImageFont

root = Path(r"f:\NORMAL\My_bot\astrbot_plugin_airi_gallery")
out_dir = root / "generated"
out_dir.mkdir(parents=True, exist_ok=True)

p2 = root / "p2.png"
p4 = root / "p4.png"


def load_font(size):
    for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def interp(a, b, r):
    return tuple(int(a[i] + (b[i] - a[i]) * r) for i in range(3))


def bg(draw, w, h, a, b):
    for y in range(h):
        r = y / max(1, h - 1)
        draw.line((0, y, w, y), fill=interp(a, b, r))


def paste_corner(canvas, path, max_size, margin=22):
    if not path.exists():
        return None
    with PILImage.open(path) as overlay:
        overlay = overlay.convert('RGBA')
        overlay.thumbnail(max_size, PILImage.Resampling.LANCZOS)
        x = max(0, canvas.width - overlay.width - margin)
        y = margin
        canvas.alpha_composite(overlay, (x, y))
        return overlay.size

# Category preview (with p2 and p4)
cats = ['表情包', '日常', '搞笑', '猫猫', '风景', '其它']
title_font = load_font(54)
subtitle_font = load_font(22)
category_font = load_font(30)
count_font = load_font(22)
cols = 3
card_w = 284
card_h = 78
gap_x = 18
gap_y = 14
padding_x = 42
padding_top = 188
padding_bottom = 44
rows = math.ceil(len(cats)/cols)
width = padding_x*2 + cols*card_w + (cols-1)*gap_x
height = padding_top + rows*card_h + (rows-1)*gap_y + padding_bottom
canvas = PILImage.new('RGBA', (width, height), (0,0,0,255))
draw = ImageDraw.Draw(canvas)
bg(draw, width, height, (255,238,246), (248,236,255))
draw.text((padding_x,48), '分类列表', fill=(57,64,100), font=title_font)
draw.text((padding_x,112), f'当前共 {len(cats)} 个分类', fill=(95,106,143), font=subtitle_font)
try:
    total_images = sum([12 for _ in cats])
    draw.text((padding_x,140), f"总图片数：{total_images}", fill=(95, 106, 143), font=subtitle_font)
except Exception:
    pass

# paste p2 and get its displayed size
p2_size = paste_corner(canvas, p2, (160,160), margin=22)
# place p4 to the left of p2 and align height
if p2_size and p4.exists():
    try:
        with PILImage.open(p4) as p4img:
            p4img = p4img.convert('RGBA')
            # thumbnail to p2 height
            p2w, p2h = p2_size
            p4img.thumbnail((p2w, p2h), PILImage.Resampling.LANCZOS)
            # try enlarging to ~2x, but cap by available space
            desired_w = int(p4img.width * 2)
            spacing = 12
            max_allowed = max(40, canvas.width - (p2w + 22) - spacing - padding_x)
            final_w = min(desired_w, max_allowed)
            final_h = max(1, int(final_w * (p4img.height / max(1, p4img.width))))
            try:
                p4_resized = p4img.resize((int(final_w), int(final_h)), PILImage.Resampling.LANCZOS)
            except Exception:
                p4_resized = p4img
            # 微调偏移：向左 / 向上 移动一些以避免与标题区域重合
            shift_left = 60
            shift_up = 12
            x = canvas.width - (p2w + 22) - spacing - p4_resized.width - shift_left
            y = 22 + max(0, (p2h - p4_resized.height)//2) - shift_up
            canvas.alpha_composite(p4_resized, (max(0,int(x)), max(0,int(y))))
    except Exception:
        pass

for index, category in enumerate(cats):
    row = index // cols
    col = index % cols
    x = padding_x + col * (card_w + gap_x)
    y = padding_top + row * (card_h + gap_y)
    row_card = PILImage.new('RGBA', (card_w, card_h), (0,0,0,0))
    rd = ImageDraw.Draw(row_card)
    rd.rounded_rectangle((0,0,card_w-1,card_h-1), radius=22, fill=(255,255,255,182), outline=(224,183,205,238), width=2)
    rd.text((20,18), category, fill=(32,38,59), font=category_font)
    count_text = '12 张'
    count_w, count_h = rd.textbbox((0,0), count_text, font=count_font)[2:4]
    rd.text((card_w-count_w-20, (card_h-count_h)/2-1), count_text, fill=(100,109,136), font=count_font)
    canvas.alpha_composite(row_card, (x,y))

cat_out = out_dir / f'preview_category_{int(time.time()*1000)}.png'
canvas.convert('RGB').save(cat_out)

# Help preview (unchanged)
help_cards = [
    ('/airi_gallery', '查看帮助说明'),
    ('看看<分类>', '从某个分类里随机返回一张图片或表情包'),
    ('看看<分类> N', '随机返回 N 张，N 最大 5，分类和数字之间要有空格'),
    ('看全部<分类>', '生成该分类的总览图，并标注每张图片的编号'),
    ('看看123', '按编号直接查看指定图片或表情包'),
    ('/分类列表', '输出漂亮的分类总览图片'),
    ('/创建<分类>', '创建一个新的分类文件夹'),
    ('/上传<分类>', '回复图片后上传到指定分类'),
    ('/删除123', '删除指定编号的图片或表情包'),
    ('/导入图库', '重新扫描并整理图库编号'),
]
card_width = 920
card_height = 92
gap = 16
padding = 42
header_h = 240
rows = len(help_cards)
width = padding*2 + card_width
height = header_h + rows*card_height + (rows-1)*gap + 42
canvas = PILImage.new('RGBA', (width, height), (0,0,0,255))
draw = ImageDraw.Draw(canvas)
bg(draw, width, height, (255,238,246), (247,235,255))
title_font = load_font(60)
subtitle_font = load_font(22)
name_font = load_font(30)
desc_font = load_font(20)
draw.text((padding,54), 'Airi 画廊插件', fill=(58,64,101), font=title_font)
draw.text((padding,126), '帮助说明 · 看命令模式随配置变化 · 管理命令用 /', fill=(98,106,140), font=subtitle_font)
draw.text((padding,160), '当前模式：no_prefix', fill=(92,98,128), font=subtitle_font)
# p1 shifted left half width
p1 = root / 'p1.png'
if p1.exists():
    with PILImage.open(p1) as p1img:
        p1img = p1img.convert('RGBA')
        p1img.thumbnail((180,180), PILImage.Resampling.LANCZOS)
        margin = 22
        x = canvas.width - p1img.width - margin - (p1img.width // 2)
        y = margin
        canvas.alpha_composite(p1img, (max(0,int(x)), max(0,int(y))))

outline_colors = [(224,183,205,238), (197,214,241,238), (206,228,201,238), (241,218,182,238)]
for index, (command, desc) in enumerate(help_cards):
    x = padding
    y = header_h + index * (card_height + gap)
    card = PILImage.new('RGBA', (card_width, card_height), (0,0,0,0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((0,0,card_width-1,card_height-1), radius=26, fill=(255,255,255,186), outline=outline_colors[index % len(outline_colors)], width=2)
    cd.text((26,16), command, fill=(35,40,61), font=name_font)
    lines = []
    # simple wrap
    cand = desc
    lines.append(cand if len(cand) < 60 else cand[:60])
    line_height = cd.textbbox((0,0), '测', font=desc_font)[3]
    for li, line in enumerate(lines[:2]):
        cd.text((26, 52 + li * (line_height + 7)), line, fill=(95,105,132), font=desc_font)
    canvas.alpha_composite(card, (x,y))

help_out = out_dir / f'preview_help_{int(time.time()*1000)}.png'
canvas.convert('RGB').save(help_out)

print(cat_out)
print(help_out)
