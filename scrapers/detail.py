"""ESJZone 详情页抓取器"""

import logging
from typing import Dict, List, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from core.scraper import BaseScraper

logger = logging.getLogger("esjzone.scraper")


class DetailScraper(BaseScraper):
    """ESJZone 小说详情页抓取器"""

    def can_handle(self, url: str) -> bool:
        return '/detail/' in url

    async def parse_novel_info(self, page) -> Dict:
        """解析小说元信息"""
        info = {
            'title': '',
            'author': '',
            'alt_title': '',
            'raw_url': '',
            'tags': [],
            'description': '',
            'cover_url': ''
        }

        title_el = page.locator('h2')
        if await title_el.count() > 0:
            info['title'] = (await title_el.first.inner_text()).strip()

        detail_items = page.locator('ul.list-unstyled.mb-2.book-detail li')
        count = await detail_items.count()
        for i in range(count):
            item = detail_items.nth(i)
            text = await item.inner_text()
            if '作者:' in text:
                author_link = item.locator('a').first
                if await author_link.count() > 0:
                    info['author'] = (await author_link.inner_text()).strip()
            elif '其他書名:' in text:
                info['alt_title'] = text.replace('其他書名:', '').strip()
            elif 'Web生肉:' in text:
                link = item.locator('a').first
                if await link.count() > 0:
                    info['raw_url'] = await link.get_attribute('href')

        desc_el = page.locator('div.description')
        if await desc_el.count() > 0:
            info['description'] = (await desc_el.inner_text()).strip()

        cover_div = page.locator('div.product-gallery.text-center.mb-3 img')
        if await cover_div.count() > 0:
            src = (await cover_div.first.get_attribute('src')
                   or await cover_div.first.get_attribute('data-src'))
            if src:
                info['cover_url'] = urljoin(page.url, src)

        # 获取「內容標籤」区域下的标签
        # DOM 结构：<section class="widget widget-tags ...">
        #             <h3 class="widget-title">內容標籤`</h3>
        #             <a class="tag" href="/tags/...">标签名</a> ...
        try:
            tag_section = page.locator('section.widget-tags, section:has(> h3.widget-title:has-text("內容標籤"))')
            if await tag_section.count() > 0:
                tag_links = tag_section.first.locator('a.tag')
                link_count = await tag_links.count()
                tags = []
                for i in range(link_count):
                    tag_text = (await tag_links.nth(i).inner_text()).strip()
                    if tag_text:
                        tags.append(tag_text)
                if tags:
                    info['tags'] = tags
                    logger.info(f"抓取到标签: {tags}")
        except Exception as e:
            logger.warning(f"获取标签失败: {e}")

        return info

    async def parse_chapter_list(self, page) -> List[Dict]:
        """解析章节列表"""
        chapters = []

        # 等待章节列表动态加载完成
        try:
            await page.wait_for_selector('#chapterList a', timeout=5000)
        except Exception:
            pass

        links = page.locator('#chapterList a')
        count = await links.count()

        # 单独的章节计数器（跳过 volume 头等非章节链接）
        chapter_idx = 0

        for i in range(count):
            link = links.nth(i)
            href = await link.get_attribute('href')

            # 跳过无意义链接（volume 折叠头、javascript 等）
            if not href or href.startswith('javascript:'):
                continue

            # 多层策略提取章节标题
            title = await self._extract_chapter_title(link)
            if not title:
                title = f"章节 {chapter_idx + 1}"
                logger.debug(
                    "无法提取 #chapterList 第 %d 个链接的标题 (href=%s)，使用回退: %s",
                    i, href[:60] if href else '', title
                )

            full_url = page.urljoin(href) if not href.startswith('http') else href
            chapters.append({'title': title, 'url': full_url})
            chapter_idx += 1

        return chapters

    async def _extract_chapter_title(self, link) -> str:
        """多层策略提取 <a> 链接的章节标题
        ESJZone 章节链接结构: <a data-title="标题" ...><p>标题</p></a>
        """
        # 策略 1: data-title 属性 → 部分页面最可靠
        title = await link.get_attribute('data-title')
        if title and title.strip():
            return title.strip()

        # 策略 2: inner_text() → Playwright 可见文本
        title = (await link.inner_text()).strip()
        if title:
            return title

        # 策略 3: text_content() → 含隐藏文本
        title = (await link.text_content()).strip()
        if title:
            return title

        # 策略 4: 提取子 <p> 元素的文本
        p_el = link.locator('p').first
        if await p_el.count() > 0:
            title = (await p_el.inner_text()).strip()
            if title:
                return title

        return ""

    async def parse_chapter_content(self, page) -> Tuple[str, List[str]]:
        """解析章节正文，返回 (html_content, [image_urls])"""
        content_div = page.locator('div.forum-content.mt-3')
        if await content_div.count() == 0:
            return '<p>未找到正文</p>', []

        if await content_div.locator('button.btn-send-pw').count() > 0:
            return '<p>本章节需要密码，已跳过</p>', []

        raw_html = await content_div.inner_html()
        soup = BeautifulSoup(raw_html, 'lxml')

        # ── 洗净 HTML：只保留可排版的内容标签 ──

        # 1. 展开无视觉意义的包装标签
        for tag in soup.find_all(['font', 'dir', 'bdi']):
            tag.unwrap()

        # 2. 处理 <span>：保留 style（颜色等）和 class，清除站点追踪属性
        for span in soup.find_all('span'):
            if span.get('dir') == 'auto':
                span.unwrap()
                continue
            # 站点专用属性全部清除
            for attr in ['data-list', 'data-id', 'data-user', 'data-s9e-mediaembed', 'data-src', 'title']:
                span.attrs.pop(attr, None)
            # 如果 span 最终无任何属性且无子元素，展开它
            if not span.attrs and len(span.contents) <= 1 and not span.find_all():
                span.unwrap()

        # 3. 清除 <div> 的站点属性
        for div in soup.find_all('div'):
            attrs_to_remove = [
                k for k in div.attrs
                if k.startswith('data-') or k in ('dir', 'tabindex')
            ]
            for attr in attrs_to_remove:
                div.attrs.pop(attr, None)
            # 空属性 div 若有 inline style 才保留 wrapper
            if not div.attrs and len(div.contents) <= 1 and not div.find_all():
                div.unwrap()

        # 4. 清除空标签（保留 <br> <hr> <img> 等自闭合标签）
        VOID_TAGS = {'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base', 'col', 'embed', 'source', 'track', 'wbr'}
        for tag in soup.find_all():
            if tag.name not in VOID_TAGS and not tag.get_text(strip=True) and not tag.find_all('img'):
                tag.decompose()

        # 5. 收集图片 URL 并改 src 指向本地
        img_tags = soup.find_all('img')
        img_urls = []
        for img in img_tags:
            src = img.get('src') or img.get('data-src')
            if src:
                full_src = urljoin(page.url, src)
                img_urls.append(full_src)
                img.attrs = {'src': full_src, 'alt': img.get('alt', '')}

        return str(soup), img_urls
