r"""EPUB 重构引擎 —— 严格遵循 Sigil / EPUB3 规范，原地修补现有 EPUB

用法:
    python tools/_regen_epub.py "novels/xxx.epub"
    python tools/_regen_epub.py "novels/xxx.epub" --dry-run     # 仅诊断
    python tools/_regen_epub.py --batch novels/                  # 批量重构

功能:
    1. 自动探测内部结构（EPUB/ 或 OEBPS/ 或其他根目录）
    2. 修复 mimetype 非首文件 / 被压缩问题（EPUB 规范硬性要求）
    3. 修复 OPF 缺 unique-identifier 属性
    4. 将 CSS 中 epub\:type 属性选择器替换为 class 选择器（消除 Sigil CSS 警告）
    5. 给 title.xhtml / nav.xhtml 的 body/nav 加 class（保留 epub:type 语义）
    6. 统一 title.xhtml 内部缩进
    7. 章节正文 <br /><br /> 分段 → <p> 段落规范化（恢复 text-indent 缩进）

设计原则:
    - 仅修改格式/结构层，不触碰图片/正文内容
    - 幂等可重入：多次运行结果一致
    - 修改前自动备份 .bak，失败时回滚
    - img 缺失等数据问题不属于重构范畴，不处理
"""
import sys
import os
import re
import zipfile
import shutil
import tempfile
import argparse

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


class EpubRefinery:
    """EPUB 重构引擎"""

    def __init__(self, epub_path: str, backup: bool = True):
        self.epub_path = epub_path
        self.backup = backup
        self.diagnostics = []          # (severity, category, message)
        self.modifications = {}       # rel_path -> new_content
        # 自动探测的内部根目录（存放 OPF 的目录）
        self.content_root = None
        self.opf_path = None

    # ── 诊断 ──
    def diagnose(self) -> list:
        """全面扫描，返回分级问题列表；同时记录可自动修复的项"""
        self.diagnostics = []
        with zipfile.ZipFile(self.epub_path, 'r') as z:
            names = set(z.namelist())
            info = z.infolist()

            # 1. mimetype
            first = info[0].filename
            if first != 'mimetype':
                self.diagnostics.append(('ERROR', 'MIMETYPE',
                    f'mimetype 非 ZIP 首文件（实际首文件: {first}）'))
            mt_raw = z.read('mimetype').decode('utf-8', errors='replace') if 'mimetype' in names else ''
            if mt_raw.strip() != 'application/epub+zip':
                self.diagnostics.append(('ERROR', 'MIMETYPE', f'mimetype 值错误: {repr(mt_raw.strip())}'))
            if info[0].compress_type != 0:
                self.diagnostics.append(('ERROR', 'MIMETYPE', f'mimetype 被压缩 (type={info[0].compress_type})'))

            # 2. container.xml → OPF 路径
            cont = z.read('META-INF/container.xml').decode('utf-8', errors='replace')
            m = re.search(r'full-path="([^"]+)"', cont)
            self.opf_path = m.group(1) if m else None
            if not self.opf_path or self.opf_path not in names:
                self.diagnostics.append(('ERROR', 'CONTAINER', f'rootfile 指向不存在: {self.opf_path}'))
                return self.diagnostics
            self.content_root = self.opf_path.rsplit('/', 1)[0] if '/' in self.opf_path else ''

            # 3. OPF 检查
            opf = z.read(self.opf_path).decode('utf-8', errors='replace')
            if 'unique-identifier=' not in opf:
                self.diagnostics.append(('WARNING', 'OPF', 'content.opf 缺 unique-identifier 属性'))
            # manifest 引用完整性
            for mm in re.finditer(r'<item[^>]*?href="([^"]+)"', opf):
                href = mm.group(1)
                full = f"{self.content_root}/{href}" if self.content_root else href
                if full not in names:
                    self.diagnostics.append(('WARNING', 'MANIFEST', f'manifest href="{href}" 文件不存在'))

            # 4. CSS 转义选择器
            css_path = self._find_file(z, names, 'stylesheet.css') or self._find_file(z, names, 'style.css')
            if css_path:
                css = z.read(css_path).decode('utf-8', errors='replace')
                if re.search(r'epub\\?:type=', css):
                    self.diagnostics.append(('WARNING', 'CSS', f'{css_path}: 含 epub:type 转义选择器（触发 Sigil 警告）'))

            # 5. Images 目录文件 vs manifest 交叉检查
            self._check_orphan_images(z, names, opf)

        return self.diagnostics

    def _find_file(self, z, names, filename):
        """在 ZIP 中查找名为 filename 的文件（忽略目录）"""
        for n in names:
            if n == filename or n.endswith('/' + filename):
                return n
        return None

    def _check_orphan_images(self, z, names, opf_text: str) -> list:
        """检查 Images/ 目录下是否有文件未被 OPF manifest 记录（Sigil 警告）

        返回孤儿图片列表 [(zip_path, relative_href), ...] 供补录 manifest 使用
        """
        # 收集 Images/ 目录下所有文件
        images_dir = f"{self.content_root}/Images" if self.content_root else "Images"
        # 兼容大小写变体 (Images / images)
        image_files = set()
        for n in names:
            parts = n.split('/')
            if len(parts) >= 2 and parts[-2].lower() == 'images':
                image_files.add(n)

        if not image_files:
            return []

        # 从 OPF manifest 提取所有 href
        manifest_hrefs = set()
        for m in re.finditer(r'<item[^>]*?href="([^"]+)"', opf_text):
            href = m.group(1).strip()
            full = f"{self.content_root}/{href}" if self.content_root else href
            manifest_hrefs.add(full)

        # 找出未记录的孤立图片
        orphans = sorted(image_files - manifest_hrefs)
        if orphans:
            if len(orphans) <= 10:
                detail = ", ".join(orphans)
            else:
                detail = f"{', '.join(orphans[:5])}, ... (共 {len(orphans)} 个)"
            self.diagnostics.append(('WARNING', 'ORPHAN_IMAGES',
                f'Images 中有 {len(orphans)} 个文件未被 manifest 记录: {detail}'))

            # 转换为 (zip_path, relative_href) 元组列表，同时存入 diagnostics 供 refine() 复用
            prefix = self.content_root + '/' if self.content_root else ''
            result = []
            for p in orphans:
                rel = p[len(prefix):] if p.startswith(prefix) else p
                result.append((p, rel))
            # 结构化数据存入 diagnostics（第3项为 list），refine() 可直接取用
            self.diagnostics.append(('DATA', 'ORPHAN_IMAGES_LIST', result))
            return result
        return []

    def _find_chapter_xhtml(self, z, names):
        """查找所有章节 xhtml（排除 title/nav/cover/colophon）"""
        skip = {'title.xhtml', 'nav.xhtml', 'cover.xhtml', 'colophon.xhtml'}
        result = []
        for n in names:
            if not n.endswith('.xhtml'):
                continue
            base = n.rsplit('/', 1)[-1]
            if base in skip:
                continue
            # 仅处理含正文（body 内有 br 或文本）的章节
            if 'chapter' in base.lower() or 'Text/' in n:
                result.append(n)
        return result

    def _patch_chapter_paragraphs(self, raw: str) -> str:
        """将章节正文中的 <br /><br /> 分段转换为 <p> 段落

        旧版生成器用连续 <br /> 分段，导致 CSS 的 p { text-indent } 失效。
        转换后恢复首行缩进阅读体验。<h1> 标题保持独立，不包裹进 <p>。
        """
        # 仅处理 body 内部
        m = re.search(r'(<body[^>]*>)(.*?)(</body>)', raw, re.S | re.I)
        if not m:
            return raw
        head, body, tail = m.group(1), m.group(2), m.group(3)
        # 若 body 内已无连续 <br /> 分段，跳过
        if not re.search(r'<br\s*/?>\s*<br\s*/?>', body, re.I):
            return raw

        # 提取并暂存 <h1> 标题（保持独立，不包裹进 <p>）
        h1_match = re.search(r'<h1[^>]*>.*?</h1>', body, re.S | re.I)
        h1_block = h1_match.group(0) if h1_match else ''
        body_without_h1 = body
        if h1_match:
            body_without_h1 = body[:h1_match.start()] + '\x00H1\x00' + body[h1_match.end():]

        # 规范化 <br /> / <br> 为统一标记
        norm = re.sub(r'<br\s*/?>', '<br/>', body_without_h1, flags=re.I)
        # 用双 <br/> 作为分段符
        blocks = re.split(r'<br/>\s*<br/>', norm)
        out_parts = []
        for blk in blocks:
            blk = blk.strip()
            if not blk or blk == '\x00H1\x00':
                continue
            # 块内残留的单 <br/> 替换为空格（段内换行转空格，符合中文排版）
            blk = re.sub(r'<br/>\s*', '', blk)
            # 清除可能的 h1 占位符残留
            blk = blk.replace('\x00H1\x00', '').strip()
            if blk:
                out_parts.append(f'    <p>{blk}</p>')
        if not out_parts:
            return raw
        new_body = (f'    {h1_block}\n' if h1_block else '') + '\n'.join(out_parts)
        return raw[:m.start()] + head + '\n' + new_body + '\n  ' + tail + raw[m.end():]

    def _inject_orphan_manifest(self, opf_text: str, orphan_images: list) -> str:
        """将孤儿图片补录到 OPF manifest

        orphan_images: [(zip_path, relative_href), ...]
        在 </manifest> 之前插入 <item> 声明，media-type 根据扩展名推断。
        """
        # 扩展名 → media-type 映射（不带点的 key，如 'jpg'）
        ext_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'svg': 'image/svg+xml', 'webp': 'image/webp',
            'bmp': 'image/bmp', 'tiff': 'image/tiff',
        }
        # 找到现有 manifest 中最大的 id 编号（避免 id 冲突）
        existing_ids = set()
        for m in re.finditer(r'<item[^>]*?id="([^"]+)"', opf_text):
            existing_ids.add(m.group(1))
        # 尝试找 img-XXXX 模式的最大编号
        max_num = 0
        for eid in existing_ids:
            nm = re.match(r'img-(\d+)', eid)
            if nm:
                max_num = max(max_num, int(nm.group(1)))

        # 先修正已存在的 octet-stream item（避免重复追加）
        new_opf = opf_text
        for zip_path, rel_href in orphan_images:
            ext = rel_href.rsplit('.', 1)[-1].lower() if '.' in rel_href else ''
            mt = ext_map.get(ext, 'image/jpeg')
            # 查找 href 匹配且 media-type=octet-stream 的 item，替换为正确类型
            pattern = re.compile(
                r'(<item[^>]*?href="' + re.escape(rel_href) + r'"[^>]*?media-type=")application/octet-stream(")',
                re.I)
            new_opf = pattern.sub(lambda m: f'{m.group(1)}{mt}{m.group(2)}', new_opf)

        # 找出仍需新增的孤儿图片（manifest 中无对应 href）
        existing_hrefs = set(re.findall(r'<item[^>]*?href="([^"]+)"', new_opf))
        items = []
        for i, (zip_path, rel_href) in enumerate(orphan_images):
            if rel_href in existing_hrefs:
                continue  # 已存在（可能是修正后的 octet-stream），跳过
            ext = rel_href.rsplit('.', 1)[-1].lower() if '.' in rel_href else ''
            mt = ext_map.get(ext, 'image/jpeg')
            num = max_num + len(items) + 1
            item_id = f'img-{num:04d}'
            item = f'      <item id="{item_id}" href="{rel_href}" media-type="{mt}"/>'
            items.append(item)

        # 在 </manifest> 结束标签前插入
        if items and '</manifest>' in new_opf:
            insert_pos = new_opf.index('</manifest>')
            new_opf = new_opf[:insert_pos] + '\n'.join(items) + '\n' + new_opf[insert_pos:]
        return new_opf

    def _fix_octet_stream_items(self, opf_text: str) -> str:
        """修正 manifest 中 media-type 为 application/octet-stream 的图片项

        旧版补录逻辑曾误用 octet-stream，Sigil 会报告「无法识别的媒体类型」。
        根据 href 扩展名推断正确 media-type 并替换。
        """
        ext_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'svg': 'image/svg+xml', 'webp': 'image/webp',
            'bmp': 'image/bmp', 'tiff': 'image/tiff',
        }
        # 匹配 media-type="application/octet-stream" 的 item，提取其 href
        def repl(m):
            full = m.group(0)
            href_m = re.search(r'href="([^"]+)"', full)
            if not href_m:
                return full
            href = href_m.group(1)
            ext = href.rsplit('.', 1)[-1].lower() if '.' in href else ''
            if ext in ext_map:
                return full.replace('application/octet-stream', ext_map[ext])
            return full

        return re.sub(r'<item[^>]*media-type="application/octet-stream"[^>]*>', repl, opf_text)

    # ── 修补 ──
    def refine(self, dry_run: bool = False, no_backup: bool = False) -> dict:
        """执行重构：诊断 → 修补 → 重新打包"""
        self.no_backup = no_backup
        self.diagnose()
        if not self.opf_path:
            return {'status': 'error', 'reason': '无法定位 OPF'}

        with zipfile.ZipFile(self.epub_path, 'r') as z:
            names = z.namelist()
            # 读取所有需要修改的文件
            modified = {}

            # A. CSS 转义选择器修复
            css_path = self._find_file(z, set(names), 'stylesheet.css') or self._find_file(z, set(names), 'style.css')
            if css_path:
                css = z.read(css_path).decode('utf-8', errors='replace')
                new_css = self._patch_css(css)
                if new_css != css:
                    modified[css_path] = new_css

            # B. title.xhtml 修补
            title_path = self._find_file(z, set(names), 'title.xhtml')
            if title_path:
                raw = z.read(title_path).decode('utf-8', errors='replace')
                new_raw = self._patch_title_xhtml(raw)
                if new_raw != raw:
                    modified[title_path] = new_raw

            # C. nav.xhtml 修补
            nav_path = self._find_file(z, set(names), 'nav.xhtml')
            if nav_path:
                raw = z.read(nav_path).decode('utf-8', errors='replace')
                new_raw = self._patch_nav_xhtml(raw)
                if new_raw != raw:
                    modified[nav_path] = new_raw

            # D. OPF unique-identifier 修复
            opf = z.read(self.opf_path).decode('utf-8', errors='replace')
            new_opf = self._patch_opf(opf)
            if new_opf != opf:
                modified[self.opf_path] = new_opf

            # E. 章节正文 <br /><br /> 分段 → <p> 段落规范化
            chapter_paths = self._find_chapter_xhtml(z, set(names))
            for ch_path in chapter_paths:
                raw = z.read(ch_path).decode('utf-8', errors='replace')
                new_raw = self._patch_chapter_paragraphs(raw)
                if new_raw != raw:
                    modified[ch_path] = new_raw

            # F. 孤儿图片补录到 OPF manifest
            opf_text = z.read(self.opf_path).decode('utf-8', errors='replace')
            # 先检查 diagnose() 是否已收集过孤儿图片（避免重复检测）
            orphan_images = None
            for d in self.diagnostics:
                if d[1] == 'ORPHAN_IMAGES_LIST' and isinstance(d[2], list):
                    orphan_images = d[2]
                    break
            if orphan_images is None:
                orphan_images = self._check_orphan_images(z, set(names), opf_text)
            if orphan_images:
                # 获取当前 OPF（可能已被 D 步修改，优先用 modified 中的版本）
                current_opf = modified.get(self.opf_path, opf_text)
                patched_opf = self._inject_orphan_manifest(current_opf, orphan_images)
                if patched_opf != current_opf:
                    modified[self.opf_path] = patched_opf
                    self.diagnostics.append(('FIX', 'ORPHAN_IMAGES',
                        f'已将 {len(orphan_images)} 个孤儿图片补录到 manifest'))

            # F2. 修正 manifest 中已存在但 media-type 为 octet-stream 的图片 item
            current_opf = modified.get(self.opf_path, opf_text)
            fixed_opf = self._fix_octet_stream_items(current_opf)
            if fixed_opf != current_opf:
                fixed_count = len(re.findall(r'octet-stream', current_opf)) - len(re.findall(r'octet-stream', fixed_opf))
                modified[self.opf_path] = fixed_opf
                self.diagnostics.append(('FIX', 'MEDIA_TYPE',
                    f'已将 {fixed_count} 个 octet-stream 图片项修正为正确 media-type'))

            # 记录需要重新打包（mimetype 首文件问题）
            need_repack = any(d[0] == 'ERROR' and d[1] == 'MIMETYPE' for d in self.diagnostics)

        if dry_run:
            return {
                'status': 'dry-run',
                'diagnostics': self.diagnostics,
                'would_modify': list(modified.keys()),
                'need_repack': need_repack,
            }

        if not modified and not need_repack:
            return {'status': 'clean', 'diagnostics': self.diagnostics}

        # 执行修改 + 重打包
        self._repackage(modified, need_repack)
        return {
            'status': 'refined',
            'diagnostics': self.diagnostics,
            'modified': list(modified.keys()),
            'repacked': need_repack,
        }

    def _patch_css(self, css: str) -> str:
        patterns = [
            (r'body\[epub\\?:type="titlepage"\]', 'body.title-page-body'),
            (r'body\[epub\\?:type="titlepage"\]\s*>\s*div', 'body.title-page-body > div'),
            (r'body\[epub\\?:type="titlepage"\]\s+h1', 'body.title-page-body h1'),
            (r'body\[epub\\?:type="titlepage"\]\s+p', 'body.title-page-body p'),
            (r'body\[epub\\?:type="titlepage"\]\s+\.novel-meta', 'body.title-page-body .novel-meta'),
            (r'nav\[epub\\?:type="toc"\]', 'nav.toc-page-nav'),
            (r'nav\[epub\\?:type="toc"\]\s+h2', 'nav.toc-page-nav h2'),
        ]
        for pat, repl in patterns:
            css = re.sub(pat, repl, css)
        return css

    def _patch_title_xhtml(self, raw: str) -> str:
        # <body epub:type="titlepage"> → 加 class
        raw = re.sub(
            r'<body\s+epub:type="titlepage">',
            '<body class="title-page-body" epub:type="titlepage">',
            raw
        )
        # 已有 class 但无 title-page-body → 追加
        def _append(m):
            cls = m.group(1)
            if 'title-page-body' in cls:
                return m.group(0)
            return f'<body class="{cls} title-page-body" epub:type="titlepage">'
        raw = re.sub(
            r'<body\s+class="([^"]*)"\s+epub:type="titlepage">',
            _append,
            raw
        )
        return raw

    def _patch_nav_xhtml(self, raw: str) -> str:
        raw = re.sub(
            r'<nav\s+epub:type="toc"',
            '<nav class="toc-page-nav" epub:type="toc"',
            raw
        )
        return raw

    def _patch_opf(self, opf: str) -> str:
        if 'unique-identifier=' in opf:
            return opf
        # 找 <package 标签，加 unique-identifier="bookid"
        def _add_uid(m):
            pkg = m.group(0)
            if 'unique-identifier=' in pkg:
                return pkg
            return pkg.replace('<package', '<package unique-identifier="bookid"', 1)
        new_opf = re.sub(r'<package[^>]*>', _add_uid, opf, count=1)
        # 确保 dc:identifier 有 id="bookid"
        if 'id="bookid"' not in new_opf:
            new_opf = re.sub(
                r'(<dc:identifier[^>]*)>',
                lambda m: m.group(1) + ' id="bookid"' if 'id=' not in m.group(1) else m.group(0),
                new_opf,
                count=1
            )
        return new_opf

    def _repackage(self, modified: dict, need_repack: bool):
        """重新打包：保留 mimetype 为 STORED 首文件"""
        tmp = tempfile.mkdtemp(prefix='epub_refine_')
        try:
            with zipfile.ZipFile(self.epub_path, 'r') as zin:
                zin.extractall(tmp)

            # 应用修改
            for rel, content in modified.items():
                full = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w', encoding='utf-8') as f:
                    f.write(content)

            # 备份（no_backup 优先级最高）
            if self.backup and not getattr(self, 'no_backup', False):
                bak = self.epub_path + '.bak'
                if os.path.exists(bak):
                    os.remove(bak)
                shutil.move(self.epub_path, bak)

            # 重新打包
            with zipfile.ZipFile(self.epub_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                # mimetype 必须第一个、STORED
                mt = os.path.join(tmp, 'mimetype')
                if os.path.exists(mt):
                    with open(mt, 'rb') as f:
                        data = f.read()
                    zout.writestr('mimetype', data, compress_type=zipfile.ZIP_STORED)
                # 其余文件
                for root, _, files in os.walk(tmp):
                    for fn in files:
                        full = os.path.join(root, fn)
                        arc = os.path.relpath(full, tmp).replace('\\', '/')
                        if arc == 'mimetype':
                            continue
                        zout.write(full, arc, compress_type=zipfile.ZIP_DEFLATED)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description='EPUB 重构工具（Sigil 合规）')
    parser.add_argument('path', help='EPUB 文件路径或 novels/ 目录')
    parser.add_argument('--dry-run', action='store_true', help='仅诊断不修改')
    parser.add_argument('--batch', action='store_true', help='批量模式（path 为目录）')
    parser.add_argument('--no-backup', action='store_true', help='不生成 .bak 备份')
    args = parser.parse_args()

    if args.batch:
        if not os.path.isdir(args.path):
            print(f"[ERR] 批量模式需要目录: {args.path}")
            sys.exit(1)
        epubs = [os.path.join(args.path, f) for f in os.listdir(args.path) if f.endswith('.epub')]
        for ep in epubs:
            print(f"\n{'='*70}\n处理: {os.path.basename(ep)}\n{'='*70}")
            r = EpubRefinery(ep, backup=not args.no_backup).refine(dry_run=args.dry_run)
            _print_result(r)
    else:
        if not os.path.exists(args.path):
            print(f"[ERR] 文件不存在: {args.path}")
            sys.exit(1)
        r = EpubRefinery(args.path, backup=not args.no_backup).refine(dry_run=args.dry_run)
        _print_result(r)


def _print_result(r: dict):
    if r['status'] == 'error':
        print(f"  [ERROR] {r['reason']}")
        return
    diags = r.get('diagnostics', [])
    if diags:
        print(f"  诊断问题 ({len(diags)}):")
        for sev, cat, msg in diags:
            print(f"    [{sev}] {cat}: {msg}")
    else:
        print("  诊断: 无问题")

    if r['status'] == 'dry-run':
        print(f"  [DRY-RUN] 将修改: {r['would_modify']}")
        print(f"  [DRY-RUN] 需重打包: {r['need_repack']}")
    elif r['status'] == 'clean':
        print("  ✅ 无需重构，已合规")
    elif r['status'] == 'refined':
        print(f"  ✅ 重构完成，修改: {r['modified']}")
        print(f"  ✅ 重打包: {r['repacked']}")


if __name__ == '__main__':
    main()
