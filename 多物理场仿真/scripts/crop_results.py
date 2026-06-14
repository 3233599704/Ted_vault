"""裁切 COMSOL 结果截图——只保留右半部分"""
from pathlib import Path
from PIL import Image

IMAGES = [
    "b5a9f7038dbe3e61c450fecf0681c566.png",
    "eaea271e78b4d916d3e044d77e780616.png",
    "50b8c656b2814aecb8d4b5943a7a54bf.png",
    "59652b95c15b3137466ae5c224327d36.jpg",
    "a055a1fae452b64b10e737bad7d980f2.jpg",
    "bc36f6b44c3b78669a73897977303d5b.jpg",
]

SRC = Path(r"D:\Staid\app\Obsidian\Ted_vault\多物理场仿真\raw\模型训练\6_10文件\comsol仿真建模")

for name in IMAGES:
    img = Image.open(SRC / name)
    w, h = img.size
    right = img.crop((w // 2, 0, w, h))
    out = SRC / f"{Path(name).stem}_right{Path(name).suffix}"
    right.save(out)
    print(f"[OK] {name} -> {out.name}  ({w//2}x{h})")

print("\nDone! Replace filenames with _right versions in inbox.")
