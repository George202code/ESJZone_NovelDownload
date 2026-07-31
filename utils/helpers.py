"""工具函数与日志配置"""

import re
import logging
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString


# URL 路径中识别的标准图片扩展名集合
KNOWN_IMAGE_EXTENSIONS: set[str] = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg',
    '.ico', '.tiff', '.tif', '.jfif', '.pjpeg', '.pjp', '.avif',
}


def clean_filename(name: str) -> str:
    """清理文件名中的非法字符，限制最大长度"""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:200]


def extract_novel_id(url: str) -> str | None:
    """从 ESJZone 详情页 URL 提取小说 ID"""
    match = re.search(r'/detail/(\d+)\.html', url)
    return match.group(1) if match else None


def apply_cjk_indent_soup(soup) -> None:
    """为正文段落添加首行缩进内联样式 ``text-indent:2em``。

    设计说明（v2.4）：
    - 早期版本在 HTML 中插入两个全角空格（U+3000），与 stylesheet.css 中的
      ``p { text-indent: 2em }`` **叠加**，导致每段向右偏约 4 个汉字宽度。
    - 当前改为 **内联 ``style="text-indent:2em"``**：
      * 不依赖外链 CSS 是否被阅读器保留（内联样式优先级最高、几乎不被剥离）；
      * 与 stylesheet.css 的 ``text-indent: 2em`` 不冲突（值相同不叠加偏移量）；
      * 老旧/受限阅读器即便丢弃外链样式表，首行缩进依然保留。

    跳过规则：
    - .colophon / .copyright-notice 容器内的段落（特殊排版区）
    - 已有 ``text-indent`` 样式的段落（避免覆盖用户自定义）
    - 空段落（无实质内容）
    """
    SKIP_PARENT_CLASSES = {'colophon', 'copyright-notice'}
    INDENT_STYLE = 'text-indent:2em'
    # 源站正文段首常自带全角空格（U+3000），若再叠加 text-indent 会造成双倍缩进
    # 因此在添加缩进样式前，先剥离段首的全角空格，避免叠加
    LEADING_IDEOGRAPHIC_SPACE = re.compile(r'^\u3000+')

    def _strip_leading_fullwidth_space(tag) -> None:
        """递归剥离块级标签文本节点开头的全角空格。"""
        for child in list(tag.children):
            if isinstance(child, NavigableString):
                stripped = LEADING_IDEOGRAPHIC_SPACE.sub('', child)
                if stripped != child:
                    child.replace_with(stripped)
                # 仅处理段首第一个文本节点
                break
            elif hasattr(child, 'children'):
                _strip_leading_fullwidth_space(child)
                break

    for p in soup.find_all('p'):
        # 规则 1：跳过特殊容器内的段落
        ancestor = p.parent
        skip = False
        while ancestor and hasattr(ancestor, 'get'):
            classes = set(ancestor.get('class', []))
            if classes & SKIP_PARENT_CLASSES:
                skip = True
                break
            ancestor = ancestor.parent
        if skip:
            continue

        # 规则 2：空段落跳过
        if not p.get_text().strip():
            continue

        # 规则 3：已有 text-indent 样式则尊重用户/源站设置
        existing_style = (p.get('style') or '').strip()
        if 'text-indent' in existing_style.lower():
            continue

        # 剥离段首全角空格，避免与 text-indent 叠加造成双倍缩进
        _strip_leading_fullwidth_space(p)

        # 合并已有 style
        new_style = (existing_style + '; ' + INDENT_STYLE).strip('; ').strip()
        p['style'] = new_style


def clean_html_for_epub(html_content: str) -> str:
    """后处理 HTML 内容，确保 EPUB 兼容性。

    处理项：
    - 移除空 <p> 标签（论坛发帖常见尾行空段）
    - 合并连续 <br> 为单个
    - 去除 JS 事件监听器（onclick 等）
    - 保留所有排版相关属性（style, class, align）
    """
    # 移除 script/iframe/embed（安全性）
    html_content = re.sub(
        r'<(script|iframe|embed|object)\b[^>]*>.*?</\1>',
        '', html_content, flags=re.DOTALL | re.IGNORECASE
    )
    html_content = re.sub(
        r'<(script|iframe|embed|object)\b[^>]*/?>',
        '', html_content, flags=re.IGNORECASE
    )

    # 清除 JS 事件属性
    html_content = re.sub(
        r'\s(on\w+)=["\'][^"\']*["\']',
        '', html_content, flags=re.IGNORECASE
    )

    # 连续 <br> 压缩为单个
    html_content = re.sub(
        r'(<br\s*/?\s*>){3,}',
        '<br/>',
        html_content, flags=re.IGNORECASE
    )

    # 空 <p> 标签移除
    html_content = re.sub(
        r'<p[^>]*>\s*</p>',
        '', html_content, flags=re.IGNORECASE
    )
    html_content = re.sub(
        r'<p[^>]*>\s*(<br\s*/?\s*>\s*)*</p>',
        '', html_content, flags=re.IGNORECASE
    )

    # 清除 HTML 注释
    html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)

    # 规范化换行（EPUB 严格要求）
    html_content = html_content.replace('\r\n', '\n').replace('\r', '\n')

    return html_content.strip()


def detect_image_format(data: bytes) -> tuple[str, str]:
    """从二进制魔术字节检测图片真实格式，返回 (extension, media_type)。

    不依赖 URL 后缀或 Content-Type 头，适用于 ESJZone CDN 返回 .file 等非标准后缀。
    """
    if len(data) < 8:
        return ('.jpg', 'image/jpeg')

    # JPEG: FF D8 FF
    if data[:3] == b'\xff\xd8\xff':
        return ('.jpg', 'image/jpeg')

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:4] == b'\x89PNG':
        return ('.png', 'image/png')

    # GIF: 47 49 46 38 (37a or 39a)
    if data[:4] == b'GIF8':
        return ('.gif', 'image/gif')

    # WebP: RIFF .... WEBP
    if data[:4] == b'RIFF' and len(data) >= 12 and data[8:12] == b'WEBP':
        return ('.webp', 'image/webp')

    # BMP: 42 4D
    if data[:2] == b'BM':
        return ('.bmp', 'image/bmp')

    # SVG（文本格式，首字节可能是空格或 BOM）
    head = data[:256].lstrip(b'\xef\xbb\xbf')  # skip BOM
    if head.startswith(b'<?xml') or head.startswith(b'<svg') or head.startswith(b'<SVG'):
        return ('.svg', 'image/svg+xml')

    # AVIF: ftypavif/avif
    if len(data) >= 12 and data[4:12] == b'ftypavif':
        return ('.avif', 'image/avif')

    # HEIC: ftypmsf1/ftypheic/ftypheix/ftyphevc/ftypheim
    if len(data) >= 12 and data[4:8] == b'ftyp' and data[8:12] in (
        b'heic', b'heix', b'hevc', b'heim', b'heis', b'hevm', b'msf1'
    ):
        return ('.heic', 'image/heic')

    # ICO: 00 00 01 00
    if data[:4] == b'\x00\x00\x01\x00':
        return ('.ico', 'image/x-icon')

    # 兜底：尝试作为 JPEG 处理（某些图片可能丢失前导字节）
    return ('.jpg', 'image/jpeg')


def resolve_image_extension(
    url: str, data: bytes | None = None
) -> tuple[str, str]:
    """综合 URL 后缀和二进制检测，返回可靠的文件扩展名、媒体类型。

    优先级：
    1. data 魔术字节检测（最可靠）
    2. URL 后缀（标准图片扩展名时）
    3. 兜底 .jpg / image/jpeg
    """
    # 先从 URL 取后缀
    url_path = url.split('?')[0].split('#')[0]
    ext_raw = os.path.splitext(url_path)[1].lower()

    # 魔术字节优先
    if data and len(data) >= 4:
        real_ext, real_mime = detect_image_format(data)
        return (real_ext, real_mime)

    # URL 后缀校验
    if ext_raw in KNOWN_IMAGE_EXTENSIONS:
        ext = '.jpg' if ext_raw == '.jpeg' else ext_raw  # 统一 jpeg→jpg
        mime_map: dict[str, str] = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp',
            '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
            '.tiff': 'image/tiff', '.tif': 'image/tiff',
            '.avif': 'image/avif', '.heic': 'image/heic',
        }
        return (ext, mime_map.get(ext, 'image/jpeg'))

    # 兜底
    return ('.jpg', 'image/jpeg')


def setup_logging(verbose: bool = False) -> logging.Logger:
    """配置日志系统：控制台输出 + 文件输出"""
    # ── Windows GBK 控制台修复：强制 UTF-8 ──
    if sys.platform == 'win32':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                try:
                    stream.reconfigure(encoding='utf-8', errors='replace')
                except Exception:
                    pass

    logger = logging.getLogger("esjzone")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%H:%M:%S"
    ))

    # 文件 handler（始终 DEBUG 级别）
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / "esjzone.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
    ))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


# 1x1 透明 PNG，用于替代下载失败的图片
PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001010300000025db56ca"
    "00000003504c5445000000a77a3dda0000000174524e530040e6d8660000000a49"
    "44415408d76360000000020001e221bc330000000049454e44ae426082"
)


def get_image_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """无依赖地从图片二进制中提取宽高。

    支持 PNG、JPEG、GIF、WebP、BMP。失败时返回 ``(None, None)``。

    实现思路：仅解析各格式的文件头，不依赖 Pillow。
    - PNG: 固定签名后第 16-24 字节为 width/height（大端）
    - JPEG: 扫描 SOF0/SOF2 标记获取尺寸
    - GIF: 逻辑屏幕描述符在第 6-10 字节
    - WebP: VP8/VP8L/VP8X chunk 解析
    - BMP: 第 18-26 字节为 width/height（小端）
    """
    if not data or len(data) < 24:
        return None, None

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        # IHDR 在 8-15 字节，长度(4) + "IHDR"(4) + width(4) + height(4)
        width = int.from_bytes(data[16:20], 'big')
        height = int.from_bytes(data[20:24], 'big')
        return (width, height) if width > 0 and height > 0 else (None, None)

    # JPEG: FF D8 FF
    if data[:3] == b'\xff\xd8\xff':
        i = 2
        while i < len(data) - 9:
            # 找到 marker
            if data[i] != 0xFF:
                i += 1
                continue
            # 跳过填充字节
            while i < len(data) and data[i] == 0xFF:
                i += 1
            if i >= len(data):
                break
            marker = data[i]
            i += 1
            # SOF0 (0xC0) / SOF1 (0xC1) / SOF2 (0xC2) / SOF3 (0xC3)
            # SOF5-SOF7 / SOF9-SOF11 / SOF13-SOF15
            # 跳过 DHT (0xC4), DNL (0xCC), DRI (0xDD), DHP (0xDE), EXP (0xDF)
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                         0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 7 <= len(data):
                    # 长度(2) + 精度(1) + 高度(2) + 宽度(2)
                    height = int.from_bytes(data[i + 3:i + 5], 'big')
                    width = int.from_bytes(data[i + 5:i + 7], 'big')
                    return (width, height) if width > 0 and height > 0 else (None, None)
                return None, None
            # 其他 marker：长度字段在 i, i+1
            if i + 2 > len(data):
                break
            seg_len = int.from_bytes(data[i:i + 2], 'big')
            i += seg_len
        return None, None

    # GIF: GIF87a / GIF89a
    if data[:6] in (b'GIF87a', b'GIF89a'):
        width = int.from_bytes(data[6:8], 'little')
        height = int.from_bytes(data[8:10], 'little')
        return (width, height) if width > 0 and height > 0 else (None, None)

    # WebP: RIFF .... WEBP
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        # VP8 (Lossy) / VP8L (Lossless) / VP8X (Extended)
        chunk = data[12:16]
        if chunk == b'VP8 ' and len(data) >= 30:
            # VP8 bitstream: 第 23-25 字节为高度，第 25-27 字节为宽度（小端）
            # 但需要检查是否含 "VP8 " 帧头标记
            try:
                w = int.from_bytes(data[26:28], 'little') & 0x3FFF
                h = int.from_bytes(data[28:30], 'little') & 0x3FFF
                return (w, h) if w > 0 and h > 0 else (None, None)
            except Exception:
                return None, None
        if chunk == b'VP8L' and len(data) >= 25:
            # VP8L: 14-bit width/height 打包在 1+3 字节
            b0 = data[21]
            b1 = data[22]
            b2 = data[23]
            b3 = data[24]
            w = ((b1 & 0x3F) << 8 | b0) + 1
            h = (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6)) + 1
            return (w, h) if w > 0 and h > 0 else (None, None)
        if chunk == b'VP8X' and len(data) >= 30:
            # VP8X: 24-bit width/height (little-endian, -1)
            w = (int.from_bytes(data[24:27], 'little') + 1)
            h = (int.from_bytes(data[27:30], 'little') + 1)
            return (w, h) if w > 0 and h > 0 else (None, None)
        return None, None

    # BMP: BM
    if data[:2] == b'BM':
        if len(data) >= 26:
            width = int.from_bytes(data[18:22], 'little')
            height = int.from_bytes(data[22:26], 'little')
            return (width, height) if width > 0 and height > 0 else (None, None)
        return None, None

    return None, None
