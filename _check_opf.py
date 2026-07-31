import zipfile, re, sys
sys.stdout.reconfigure(encoding='utf-8')
epub = 'novels/在游戏中与雌性角色交合.epub'
z = zipfile.ZipFile(epub)
opf = z.read('OEBPS/content.opf').decode('utf-8', errors='replace')
octet = re.findall(r'<item[^>]*media-type="application/octet-stream"[^>]*>', opf)
print(f'octet-stream items: {len(octet)}')
for o in octet[:5]:
    print(' ', o)
# 统计 img- 开头的 id
img_items = re.findall(r'<item[^>]*id="img-\d+"[^>]*>', opf)
print(f'\nimg-XXXX items: {len(img_items)}')
# 检查是否有重复 Images 引用
hrefs = re.findall(r'href="(Images/[^"]+)"', opf)
print(f'Images hrefs 总数: {len(hrefs)} | 唯一: {len(set(hrefs))}')
