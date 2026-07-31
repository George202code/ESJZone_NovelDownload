"""EPUB 电子书生成器 —— 专业排版 + 完整元数据"""

import hashlib
import html as html_mod
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ebooklib import epub

from utils.helpers import PLACEHOLDER_PNG, detect_image_format

logger = logging.getLogger("esjzone.epub")


# ======================== CSS 样式表 ========================
# 设计目标：移动端手机阅读优先，兼容 320-430px 视口
# 中文排版专项优化：字体栈、行距、两端对齐、标点处理
#
# 缩进策略（v2.4 更新，去除全角空格叠加问题）：
#   1. CSS text-indent: 2em          → 主力方案，所有现代阅读器支持
#   2. HTML <p style="text-indent:2em"> → 兜底方案，优先级最高、几乎不被阅读器剥离
#   与旧版「全角空格兜底」不同：全角空格 + CSS text-indent 会叠加造成首行
#   向右偏 ~4 个汉字宽度，现已改为内联样式兜底（值与 CSS 相同，不再叠加）。
BOOK_CSS = r'''
@namespace epub "http://www.idpf.org/2007/ops";

/* ============================================================
   1. 全局基础 —— 移动端中文阅读核心
   ============================================================ */
body {
    /* ── 字体栈：系统原生优先，回退链完整 ── */
    /* iOS/macOS: PingFang SC; Android: Noto Sans CJK SC; Windows: Microsoft YaHei */
    font-family:
        "PingFang SC", "Hiragino Sans GB",
        "Microsoft YaHei", "Noto Sans CJK SC",
        "Source Han Sans SC", "WenQuanYi Micro Hei",
        sans-serif;
    font-size: 1.1em;
    line-height: 1.85;
    /* inherit 让深色模式阅读器自动适配文字色 */
    color: inherit;
    background: transparent;
    margin: 0;
    padding: 0 0.3em;            /* 极窄侧边距，防文字贴屏边 */
    /* ── 断词/断字控制 ── */
    -epub-hyphens: none;
    -webkit-hyphens: none;
    hyphens: none;
    /* ── 中文两端对齐（多引擎兼容） ── */
    text-align: justify;
    text-justify: inter-ideograph;
    -epub-text-justify: inter-ideograph;
    -webkit-text-justify: inter-ideograph;
    /* ── 防止 URL / 长英文溢出 ── */
    overflow-wrap: break-word;
    /* ── 字体平滑渲染 ── */
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* ============================================================
   2. 标题层级 —— 清晰的视觉层次
   ============================================================ */
h1 {
    text-align: center;
    font-size: 1.65em;
    font-weight: 700;
    margin: 0.55em 0 0.3em;          /* 紧凑上边距，小屏不浪费空间 */
    padding-bottom: 0.15em;
    border-bottom: 1px solid rgba(128, 128, 128, 0.25);
    line-height: 1.35;
    page-break-before: always;
    letter-spacing: 0.02em;
}
h1:first-of-type { page-break-before: avoid; }

h2 {
    text-align: left;
    font-size: 1.35em;
    font-weight: 600;
    margin: 1.5em 0 0.5em;
    padding-left: 0.5em;
    border-left: 3px solid;
    border-left-color: rgba(128, 128, 128, 0.4);
    line-height: 1.4;
}

h3 {
    font-size: 1.18em;
    font-weight: 600;
    margin: 1.3em 0 0.45em;
    line-height: 1.45;
}

h4, h5, h6 {
    font-size: 1.08em;
    font-weight: 600;
    margin: 1.1em 0 0.35em;
    line-height: 1.5;
}

/* ============================================================
   3. 正文段落 —— 统一缩进 + 两端对齐
      text-indent: 2em 确保所有段落首行缩进一致
      全角空格兜底：老旧阅读器即使忽略 CSS 也保有缩进效果
   ============================================================ */
p {
    margin: 0.5em 0;
    /* ── 首行缩进 2em，所有段落一致 ── */
    text-indent: 2em;
    /* ── 中文两端对齐（多引擎） ── */
    text-align: justify;
    text-justify: inter-ideograph;
    -epub-text-justify: inter-ideograph;
    -webkit-text-justify: inter-ideograph;
    /* ── 孤儿行/寡行控制 ── */
    orphans: 2;
    widows: 2;
}

/* 无缩进段落（版权、尾页等特殊段落用 class 覆盖） */
.colophon p,
.copyright-notice p {
    text-indent: 0;
}

/* ============================================================
   4. 引用块 —— 优雅低干扰的左边框样式
   ============================================================ */
blockquote {
    margin: 0.9em 0.3em;
    padding: 0.6em 0.8em;
    border-left: 3px solid;
    border-left-color: rgba(128, 128, 128, 0.3);
    /* 微弱灰底，深色模式下仅留边框辨识 */
    background: rgba(128, 128, 128, 0.05);
    font-size: 0.93em;
    line-height: 1.75;
}
blockquote p {
    margin: 0.35em 0;
}

/* 嵌套引用（论坛多层引用） */
blockquote blockquote {
    border-left-color: rgba(128, 128, 128, 0.2);
    background: rgba(128, 128, 128, 0.03);
    font-size: 1em;
}
blockquote blockquote blockquote {
    border-left-color: rgba(128, 128, 128, 0.12);
    background: transparent;
    font-size: 1em;
}

/* ============================================================
   5. 水平分隔线 —— 场景/小节分隔符
   ============================================================ */
hr {
    border: none;
    border-top: 1px solid rgba(128, 128, 128, 0.2);
    margin: 1.8em 1.5em;
    text-align: center;
    overflow: visible;
}
hr::after {
    content: "\2731  \2731  \2731";  /* ✱  ✱  ✱ */
    position: relative;
    top: -0.65em;
    background: transparent;
    color: rgba(128, 128, 128, 0.4);
    font-size: 1em;
    padding: 0 0.6em;
}

/* ============================================================
   6. 语义内联元素
   ============================================================ */
em, i, cite, dfn { font-style: italic; }
strong, b       { font-weight: 700; }
small, sub, sup { font-size: 0.82em; }
del, s, strike  { text-decoration: line-through; opacity: 0.7; }
ins, u          { text-decoration: underline; }
mark            { background: rgba(255, 235, 140, 0.45); color: inherit; }
sub             { vertical-align: sub; }
sup             { vertical-align: super; }
abbr[title]     { border-bottom: 1px dotted rgba(128, 128, 128, 0.5); }

/* ============================================================
   7. 超链接 —— 移动端友好的触摸目标
   ============================================================ */
a {
    color: inherit;
    text-decoration: underline;
    text-decoration-color: rgba(96, 125, 139, 0.5);
    text-underline-offset: 0.15em;
    /* 增大行内触摸区域 */
    padding: 0.15em 0;
}
a:link    { color: #2a6496; }
a:visited { color: #6c4a8a; }

/* ============================================================
   8. 列表
   ============================================================ */
ul, ol {
    margin: 0.5em 0 0.5em 0.5em;
    padding-left: 1.2em;
}
li {
    margin: 0.25em 0;
    line-height: 1.75;
}
li p {
    margin: 0.2em 0;
}

/* ============================================================
   9. 代码与预格式化
   ============================================================ */
code, kbd, samp {
    font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace;
    font-size: 0.88em;
    background: rgba(128, 128, 128, 0.08);
    padding: 0.08em 0.25em;
    border-radius: 3px;
    word-break: break-all;
}
pre {
    font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace;
    font-size: 0.85em;
    line-height: 1.45;
    background: rgba(128, 128, 128, 0.06);
    border: 1px solid rgba(128, 128, 128, 0.15);
    padding: 0.7em 0.8em;
    margin: 0.7em 0;
    white-space: pre-wrap;
    overflow-wrap: break-word;
    border-radius: 4px;
}

/* ============================================================
   10. 表格 —— 移动端优化
   ============================================================ */
.table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    margin: 0.8em 0;
}
table {
    border-collapse: collapse;
    margin: 0.8em auto;
    width: auto;
    max-width: 100%;
    font-size: 0.85em;
    line-height: 1.55;
}
th, td {
    border: 1px solid rgba(128, 128, 128, 0.25);
    padding: 0.35em 0.55em;
    vertical-align: top;
}
th {
    background: rgba(128, 128, 128, 0.08);
    font-weight: 600;
    text-align: center;
}
td { text-align: left; }

/* ============================================================
   11. 插图 —— 移动端屏幕适配
   ============================================================ */
img {
    max-width: 100%;
    height: auto;
    /* 限制极高图片（如竖版漫画条）显示高度 */
    max-height: 85vh;
    object-fit: contain;
    display: block;
    margin: 0.9em auto;
    page-break-inside: avoid;
    /* 图片不可选中（提升阅读沉浸感） */
    -webkit-user-select: none;
    user-select: none;
}

figure {
    margin: 1em 0;
    text-align: center;
    page-break-inside: avoid;
}
figure img {
    margin-bottom: 0.3em;
}
figcaption {
    font-size: 0.82em;
    opacity: 0.7;
    margin-top: 0.2em;
}

/* ============================================================
   12. 日文注音 (ruby) —— 保留以兼容 ESJZone 日轻内容
   ============================================================ */
ruby {
    ruby-align: center;
}
rp { display: none; }
rt {
    font-size: 0.48em;
    opacity: 0.65;
    line-height: 1.1;
    letter-spacing: -0.02em;
}

/* ============================================================
   13. 特殊页样式
   ============================================================ */

/* 书籍尾页 */
.colophon {
    margin-top: 3.5em;
    padding-top: 1.2em;
    border-top: 1px solid rgba(128, 128, 128, 0.2);
    font-size: 0.85em;
    opacity: 0.75;
    text-align: center;
    line-height: 1.9;
}
.colophon p { margin: 0.25em 0; }

/* 版权声明框 */
.copyright-notice {
    margin: 2em 0;
    padding: 1em 1em;
    border: 1px solid rgba(128, 128, 128, 0.2);
    background: rgba(128, 128, 128, 0.04);
    font-size: 0.9em;
    opacity: 0.8;
    text-align: center;
    border-radius: 4px;
}
.copyright-notice p { margin: 0.3em 0; }

/* ============================================================
   14. 移动端响应式微调
   ============================================================ */

/* 小屏手机 (<=360px): 紧凑排版，去掉 letter-spacing 以改善对齐 */
@media (max-width: 360px) {
    body {
        font-size: 1.05em;
        line-height: 1.8;
        letter-spacing: 0;
        padding: 0 0.2em;
    }
    h1 { font-size: 1.5em; margin-top: 1.2em; }
    h2 { font-size: 1.2em; }
    p { text-indent: 1.8em; }
    blockquote { margin: 0.7em 0; padding: 0.5em 0.65em; }
    img { margin: 0.6em auto; }
}

/* 平板/大屏 (>=768px): 增加侧边留白 */
@media (min-width: 768px) {
    body {
        padding: 0 1.5em;
        font-size: 1.15em;
        line-height: 1.9;
    }
    h1 { font-size: 2em; margin-top: 0.8em; }
    img { max-height: 80vh; }
}

/* ============================================================
   15. 书名页 (title.xhtml) 现代简约样式
      使用 class="title-page-body" 而非 body[epub:type="titlepage"] 属性选择器，
      避免 CSS 转义冒号在某些 EPUB 编辑器（如 Sigil）中触发警告。
   ============================================================ */
body.title-page-body,
body[role="doc-titlepage"] {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 90vh;
    text-align: center;
    padding: 2em 1em;
}
body.title-page-body > div,
body[role="doc-titlepage"] > div {
    max-width: 34em;
}

/* 书名页标题 */
body.title-page-body h1,
body[role="doc-titlepage"] h1 {
    border-bottom: none !important;
    page-break-before: avoid !important;
    font-size: 1.75em;
    letter-spacing: 0.03em;
    line-height: 1.35;
    margin-bottom: 0.4em;
}

/* 书名页正文段落（作者、来源等）—— 无缩进 */
body.title-page-body p,
body[role="doc-titlepage"] p {
    text-indent: 0 !important;
    font-size: 0.92em;
    color: #666;
    margin: 0.2em 0;
}

/* 书名页元信息行 */
body.title-page-body .novel-meta,
body[role="doc-titlepage"] .novel-meta {
    font-size: 0.85em;
    color: #999;
    margin-top: 0.15em;
}

/* 标签胶囊容器 */
.tag-list {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.4em;
    margin: 0.5em 0 0.3em;
}

/* 标签胶囊 */
.tag-badge {
    display: inline-block;
    padding: 0.2em 0.7em;
    font-size: 0.8em;
    line-height: 1.5;
    border-radius: 99px;
    background: #f0f0f0;
    color: #555;
    border: 1px solid #e0e0e0;
    text-indent: 0 !important;
    white-space: nowrap;
    font-weight: 400;
}
'''


# ======================== 错误报告 ========================
class ErrorReporter:
    """收集并汇总下载过程中的错误"""

    def __init__(self):
        self.errors: list = []

    def add(self, category: str, message: str, url: str = '') -> None:
        self.errors.append({'category': category, 'message': str(message)[:200], 'url': url})

    def report(self) -> None:
        if not self.errors:
            logger.info("✅ 没有发生错误")
            return
        logger.warning("错误报告 (共 %d 条):", len(self.errors))
        for i, err in enumerate(self.errors, 1):
            logger.warning("  %d. [%s] %s", i, err['category'], err['message'])
            if err['url']:
                logger.warning("     URL: %s", err['url'])

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ======================== 内部工具 ========================
def _deterministic_uuid(novel_id: str, title: str) -> str:
    """从 novel_id + title 生成确定性 UUID，保证同一本书重新下载时 UUID 一致"""
    seed = f"esjzone:{novel_id}:{title}"
    h = hashlib.sha256(seed.encode()).hexdigest()[:32]
    return str(uuid.UUID(h))


def _tags_to_description(tags: List[str]) -> Optional[str]:
    """将标签列表转为一句话描述（用于 DC:description）"""
    if not tags:
        return None
    return "标签: " + "、".join(tags[:10])  # 前10个标签


# ======================== EPUB 生成 ========================
def generate_epub(
    state,
    chapters_content: List[Dict],
    cover_image_bytes: Optional[bytes],
    output_dir: str,
    error_reporter: ErrorReporter,
    failed_images: List[Dict],
    cover_dimensions: tuple[int | None, int | None] = (None, None),
) -> str:
    """
    生成专业排版的 EPUB 电子书。

    参数：
        state: NovelState 对象
        chapters_content: [{"title": str, "content": str, "images": List}, ...]
        cover_image_bytes: 封面图片 bytes（可为 None）
        output_dir: 输出目录
        error_reporter: 错误收集器
        failed_images: [{"filename": str, "url": str}, ...]
        cover_dimensions: (width, height) 封面真实像素尺寸；用于生成正确比例的 SVG viewBox

    返回：
        EPUB 文件路径
    """
    book = epub.EpubBook()

    # ================== 元数据 ==================
    book_uid = _deterministic_uuid(state.novel_id, state.title)
    book.set_identifier(book_uid)
    book.set_title(state.title)
    book.set_language('zh')

    if state.author:
        book.add_author(state.author)

    # 副标题
    if state.alt_title:
        book.add_metadata(
            None, 'meta', '',
            {'name': 'calibre:title_sort', 'content': state.alt_title}
        )

    # 来源 URL
    if state.raw_url:
        book.add_metadata('DC', 'source', state.raw_url)

    # 描述（标签）
    description = _tags_to_description(state.tags)
    if description:
        book.add_metadata('DC', 'description', description)

    # 出版者 + 日期
    book.add_metadata('DC', 'publisher', 'ESJZone (自动下载)')
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    book.add_metadata('DC', 'date', now)

    # EPUB3 属性：封面页标记
    book.add_metadata(
        None, 'meta', '',
        {'property': 'dcterms:modified', 'content': now}
    )

    # ================== CSS ==================
    nav_css = epub.EpubItem(
        uid="style_default",
        file_name="Styles/stylesheet.css",
        media_type="text/css",
        content=BOOK_CSS.encode('utf-8')
    )
    book.add_item(nav_css)

    # 目录页专属样式（独立于正文，避免 h2/ol 默认样式干扰）
    NAV_CSS = '''
/* 目录页容器 */
nav.toc-page-nav,
nav[role="doc-toc"] {
    padding: 2em 1.2em;
    max-width: 42em;
    margin: 0 auto;
}

/* 目录标题（居中 + 下间距） */
nav.toc-page-nav h2,
nav[role="doc-toc"] h2 {
    text-align: center;
    font-size: 1.4em;
    font-weight: 700;
    color: #222222;
    margin: 0 0 1em;
    padding-bottom: 0.3em;
    letter-spacing: 0.05em;
}

/* 顶级目录列表（无默认序号 + 左内边距清零） */
nav ol {
    list-style: none;
    margin: 0;
    padding: 0;
}

/* 章节项（虚线分隔） */
nav li {
    margin: 0;
    border-bottom: 1px dotted #e0e0e0;
}

nav li a {
    display: block;
    padding: 0.3em 0;
    text-decoration: none;
    color: #222222;
    line-height: 1.6;
    transition: color 0.15s;
}

nav li a:hover {
    color: #000000;
}

/* 自定义章节号（行内灰色小号，替代 EPUB 默认 1. 2. 3. 冗余序号） */
nav li a::before {
    content: attr(data-num);
    color: #999999;
    margin-right: 0.5em;
    font-size: 0.9em;
    font-variant-numeric: tabular-nums;
}

/* 分卷/分组容器（每100章一组） */
nav ol ol {
    margin-left: 0;
}

nav ol ol li a {
    padding-left: 1.2em;
    font-size: 0.97em;
}

/* 分组标题（卷名，作为分卷分隔，上虚线下间距） */
nav ol > li.group-title {
    border-bottom: none;
    margin-top: 0.6em;
}

nav ol > li.group-title > span {
    display: block;
    padding: 0.8em 0 0.4em;
    margin-bottom: 0.2em;
    border-bottom: 1px dotted #cccccc;
    font-weight: 700;
    font-size: 1.02em;
    color: #666666;
    letter-spacing: 0.03em;
}
'''

    nav_style = epub.EpubItem(
        uid="nav_style",
        file_name="Styles/nav.css",
        media_type="text/css",
        content=NAV_CSS.encode('utf-8')
    )
    book.add_item(nav_style)

    spine: list = ['nav']
    toc: list = []

    # ================== 封面页 ==================
    if cover_image_bytes:
        # 注册封面图片到 manifest（EPUB3 cover-image 属性）
        cover_img_item = epub.EpubImage(
            uid='cover-img',
            file_name='images/cover.jpg',
            media_type='image/jpeg',
            content=cover_image_bytes
        )
        cover_img_item.properties = 'cover-image'
        book.add_item(cover_img_item)

        # 封面 HTML —— 直接用 <img> 引用图片，浏览器原生渲染
        # 不再用 SVG 包装，原因：
        #   - SVG preserveAspectRatio="xMidYMid meet" 在部分阅读器
        #     （如 Sigil 预览窗口）被错误解析，导致图片顶部被裁切
        #   - 大量阅读器（Readest/Calibre 等）将 <svg><image/> 当作
        #     复杂结构而不触发 cover-image 渲染，回退为占位符
        # 直接用 <img>：图片按宽度自适应，高度自动等比缩放，
        # 永远不会裁切或变形，所有阅读器均能正确显示
        cover_html = epub.EpubHtml(
            uid='cover-page',
            title='封面',
            file_name='cover.xhtml',
            lang='zh'
        )
        cover_html.set_content('''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>封面</title>
    <style>
        html, body { margin: 0; padding: 0; height: 100%; text-align: center; }
        body { display: flex; align-items: center; justify-content: center; }
        img.cover {
            display: block;
            max-width: 100%;
            max-height: 100%;
            width: auto;
            height: auto;
            margin: 0 auto;
        }
    </style>
</head>
<body epub:type="cover">
    <img class="cover" src="images/cover.jpg" alt="封面"/>
</body>
</html>''')
        book.add_item(cover_html)
        cover_html.add_link(href='Styles/stylesheet.css', rel='stylesheet', type='text/css')
        spine.insert(0, cover_html)

    # ================== 版权声明页（书名页） ==================
    title_page = epub.EpubHtml(
        uid='title-page',
        title='书名页',
        file_name='title.xhtml',
        lang='zh'
    )

    # 构建标签胶囊 HTML
    tags_html = ''
    if state.tags:
        badges = ''.join(
            f'<span class="tag-badge">{html_mod.escape(t)}</span>'
            for t in state.tags[:12]
        )
        tags_html = f'<div class="tag-list">{badges}</div>'

    author_line = f'<p class="novel-author">作者：{html_mod.escape(state.author)}</p>' if state.author else ''
    alt_line = f'<p class="novel-meta">（{html_mod.escape(state.alt_title)}）</p>' if state.alt_title else ''
    source_line = f'<p class="novel-meta">来源：<a href="{html_mod.escape(state.raw_url, quote=True)}">{html_mod.escape(state.raw_url[:80])}</a></p>' if state.raw_url else ''

    title_page.set_content(f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <meta charset="utf-8"/>
    <link href="Styles/stylesheet.css" rel="stylesheet" type="text/css"/>
</head>
<body class="title-page-body" epub:type="titlepage">
<div>
    <h1>{html_mod.escape(state.title)}</h1>
{alt_line}
{author_line}
{tags_html}
{source_line}
    <div class="copyright-notice">
        <p>本书由 ESJZone 爬虫自动生成，仅供个人学习使用。</p>
        <p>请支持正版，前往 ESJZone 阅读原文。</p>
        <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    </div>
</div>
</body>
</html>''')
    book.add_item(title_page)
    title_page.add_link(href='Styles/stylesheet.css', rel='stylesheet', type='text/css')
    spine.append(title_page)
    toc.append(title_page)

    # ================== 失败图片占位图 ==================
    for img_info in failed_images:
        placeholder_item = epub.EpubImage()
        placeholder_item.file_name = img_info['filename']
        placeholder_item.content = PLACEHOLDER_PNG
        placeholder_item.media_type = 'image/png'
        book.add_item(placeholder_item)

    # ================== 章节 ==================
    for idx, ch in enumerate(chapters_content, 1):
        filename = f"chapter_{idx:03d}.xhtml"
        html_item = epub.EpubHtml(
            uid=f'ch{idx:03d}',
            title=ch['title'],
            file_name=filename,
            lang='zh'
        )
        content = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <link href="Styles/stylesheet.css" rel="stylesheet" type="text/css"/>
    <title>{html_mod.escape(ch['title'])}</title>
</head>
<body epub:type="chapter">
    <h1>{html_mod.escape(ch['title'])}</h1>
    {ch['content']}
</body>
</html>'''
        html_item.set_content(content)
        book.add_item(html_item)
        html_item.add_link(href='Styles/stylesheet.css', rel='stylesheet', type='text/css')
        spine.append(html_item)
        toc.append(html_item)

        # 嵌入章节图片
        for img_data in ch.get('images', []):
            if img_data.get('data'):
                img_item = epub.EpubImage()
                img_item.file_name = f"images/{img_data['filename']}"
                img_item.content = img_data['data']
                img_item.media_type = img_data['media_type']
                book.add_item(img_item)

    # ================== 书籍尾页 ==================
    colophon_page = epub.EpubHtml(
        uid='colophon',
        title='尾页',
        file_name='colophon.xhtml',
        lang='zh'
    )
    completed_count = len(chapters_content)
    colophon_page.set_content(f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <meta charset="utf-8"/>
    <link href="Styles/stylesheet.css" rel="stylesheet" type="text/css"/>
</head>
<body epub:type="colophon">
    <div class="colophon">
        <hr/>
        <p>《{html_mod.escape(state.title)}》全书完</p>
        <p>共 {completed_count} 章 · {state.total} 章收录</p>
        <p>由 ESJZone 下载器自动生成</p>
        <p>{datetime.now().strftime('%Y-%m-%d')}</p>
    </div>
</body>
</html>''')
    book.add_item(colophon_page)
    colophon_page.add_link(href='Styles/stylesheet.css', rel='stylesheet', type='text/css')
    spine.append(colophon_page)

    # ================== 导航结构 ==================
    book.toc = toc
    book.spine = spine

    # NCX 导航（兼容旧阅读器）
    book.add_item(epub.EpubNcx())

    # EPUB3 导航文档（自定义 HTML，支持分组 + 美化样式）
    # 分组策略：每 100 章为一卷（卷1: 1-100, 卷2: 101-200 ...）
    GROUP_SIZE = 100
    total_ch = len(chapters_content)

    nav_ol_parts = []
    if total_ch > GROUP_SIZE:
        # 长目录：分卷
        for g_start in range(0, total_ch, GROUP_SIZE):
            g_end = min(g_start + GROUP_SIZE, total_ch)
            vol_num = g_start // GROUP_SIZE + 1
            nav_ol_parts.append(
                f'        <li class="group-title"><span>第 {vol_num} 卷（第 {g_start+1}-{g_end} 章）</span>\n'
                f'          <ol>'
            )
            for i in range(g_start, g_end):
                ch = chapters_content[i]
                num = i + 1
                nav_ol_parts.append(
                    f'            <li><a href="chapter_{num:03d}.xhtml" '
                    f'data-num="{num}">{html_mod.escape(ch["title"])}</a></li>'
                )
            nav_ol_parts.append('          </ol>\n        </li>')
    else:
        # 短目录：平铺
        for idx, ch in enumerate(chapters_content, 1):
            nav_ol_parts.append(
                f'        <li><a href="chapter_{idx:03d}.xhtml" '
                f'data-num="{idx}">{html_mod.escape(ch["title"])}</a></li>'
            )

    nav_ol_html = '\n'.join(nav_ol_parts)

    nav_html = epub.EpubHtml(
        uid='nav',
        title='目录',
        file_name='nav.xhtml',
        lang='zh'
    )
    nav_html.set_content(f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh" xml:lang="zh">
<head>
    <meta charset="utf-8"/>
    <title>{html_mod.escape(state.title)} - 目录</title>
    <link href="Styles/stylesheet.css" rel="stylesheet" type="text/css"/>
    <link href="Styles/nav.css" rel="stylesheet" type="text/css"/>
</head>
<body>
    <nav class="toc-page-nav" epub:type="toc" id="toc" role="doc-toc">
        <h2>{html_mod.escape(state.title)}</h2>
        <ol>
{nav_ol_html}
        </ol>
    </nav>
</body>
</html>''')
    book.add_item(nav_html)
    nav_html.add_link(href='Styles/stylesheet.css', rel='stylesheet', type='text/css')
    nav_html.add_link(href='Styles/nav.css', rel='stylesheet', type='text/css')

    # ================== 写入文件 ==================
    os.makedirs(output_dir, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', state.title)
    epub_path = os.path.join(output_dir, f"{safe_title}.epub")

    # 写入选项
    opts = {
        'epub2_guide': True,   # 生成 <guide>（EPUB2 兼容）
        'epub3_landmark': True,  # EPUB3 landmarks
    }
    epub.write_epub(epub_path, book, opts)
    logger.info("EPUB 已生成: %s (%d 章)", epub_path, completed_count)
    return epub_path
