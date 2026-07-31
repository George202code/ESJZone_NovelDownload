import zipfile
import sys

epub_path = sys.argv[1]
z = zipfile.ZipFile(epub_path)
content = z.read('EPUB/cover.xhtml').decode('utf-8')
print('=== cover.xhtml 内容 ===')
print(content)
print()
print('=== EPUB/images/cover.jpg 大小 ===')
img_data = z.read('EPUB/images/cover.jpg')
print(f'{len(img_data)} bytes')
print()

# 读取封面图片实际尺寸（手动解析 JPEG）
import io
def get_jpeg_dimensions(data):
    i = 2  # 跳过 SOI
    while i < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i+1]
        if marker in (0xC0, 0xC1, 0xC2):  # SOF0/1/2
            h = (data[i+5] << 8) | data[i+6]
            w = (data[i+7] << 8) | data[i+8]
            return w, h
        if marker == 0xD8 or marker == 0xD9:
            return None
        length = (data[i+2] << 8) | data[i+3]
        i += 2 + length
    return None

dim = get_jpeg_dimensions(img_data)
print(f'=== 封面实际尺寸 === {dim}')