"""Analyze chart image and extract text via OCR."""
import sys
sys.path.insert(0, r'D:\Staid\app\Obsidian\Ted_vault')

from PIL import Image
import os

input_path = r'D:\Staid\app\Obsidian\Ted_vault\a24050df31ee7a915e6c59a84ab9d9f4.png'
output_path = r'D:\Staid\app\Obsidian\Ted_vault\_temp_chart_small.png'

img = Image.open(input_path)
print(f'Original size: {img.size}')

# Resize to 25% for readability
w, h = img.size
small = img.resize((w // 4, h // 4), Image.LANCZOS)
small.save(output_path)
print(f'Resized: {small.size}')
print(f'Resized file size: {os.path.getsize(output_path)} bytes')

# Also save as JPEG
jpg_path = r'D:\Staid\app\Obsidian\Ted_vault\_temp_chart.jpg'
small.save(jpg_path, 'JPEG', quality=85)
print(f'JPEG size: {os.path.getsize(jpg_path)} bytes')
print('Done!')
