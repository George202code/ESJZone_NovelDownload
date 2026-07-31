"""核心下载编排器 —— 浏览器管理、Page 池、并发下载、EPUB 生成"""

import asyncio
import hashlib
import logging
import os
import random
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

# 重启模式：供 GUI / CLI 指定“发现已有记录”时的行为
RESUME_MODE = "resume"    # 断点续传：保留已有状态与缓存，继续下载
RESTART_MODE = "restart"  # 清理旧数据：删除状态+缓存后重新下载
ASK_MODE = "ask"          # 询问：非交互模式用 auto_yes 决定，交互模式用 input 询问

from core.cache import CacheManager
from core.epub import generate_epub, ErrorReporter
from core.state import StateManager, NovelState, ChapterState
from scrapers.detail import DetailScraper
from scrapers.registry import get_scraper
from utils.helpers import extract_novel_id, clean_html_for_epub, resolve_image_extension, apply_cjk_indent_soup, get_image_dimensions

logger = logging.getLogger("esjzone.downloader")


# ======================== 图片下载 ========================
async def _download_image(url: str, _context: BrowserContext | None = None, timeout: int = 90000,
                         proxy: str | None = None) -> bytes | None:
    """使用 httpx 直接下载图片，完全绕过浏览器网络栈。

    不再依赖 Playwright 的 page.goto() 或 route 拦截，因此不受
    ``Content-Disposition: attachment``、``application/octet-stream``
    等服务器响应头影响。

    参数：
        proxy: 可选代理地址 (如 ``http://127.0.0.1:7890``)，用于加速
               图片 CDN 加载。传 ``None`` 表示直连。
               代理不可用时自动回退到直连。
    """
    client_kwargs_base = {
        'timeout': timeout / 1000,  # ms → s
        'follow_redirects': True,
        'headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/149.0.0.0 Safari/537.36'
            ),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        },
    }

    async def _try_fetch(use_proxy: bool) -> bytes | None:
        kwargs = {**client_kwargs_base}
        if use_proxy and proxy:
            kwargs['proxy'] = proxy
            # 代理连接用极短超时：连不上（如 Clash 未启动）立即回退直连，
            # 避免每张图都等满 45s 代理超时。读取/总超时仍用主 timeout。
            kwargs['timeout'] = httpx.Timeout(
                connect=3.0,
                read=client_kwargs_base['timeout'],
                write=client_kwargs_base['timeout'],
                pool=client_kwargs_base['timeout'],
            )
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.content
                if len(data) > 64:
                    return data
                logger.debug('图片过小 %s: %d bytes', url[:80], len(data))
            return None

    # ── 有代理时：先走代理，连接失败则回退直连 ──
    if proxy:
        try:
            result = await _try_fetch(use_proxy=True)
            if result is not None:
                return result
            # 代理可达但返回空/小文件，跳过回退（可能是真实错误）
            return None
        except (httpx.ConnectError, httpx.ProxyError):
            logger.debug('代理 %s 不可达，回退直连: %s', proxy, url[:80])
        except Exception:
            return None

    # ── 无代理 或 代理回退 → 直连 ──
    try:
        return await _try_fetch(use_proxy=False)
    except Exception as e:
        logger.debug('图片下载失败 %s: %s', url[:80], e)
        return None


# ======================== 图片并发下载任务 ========================
async def _download_images_batch(
    img_srcs: List[str],
    context: BrowserContext,
    timeout: int,
    max_concurrent: int = 5,
    proxy: str | None = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Optional[bytes]]:
    """并发下载一批图片，返回 {src: data_or_None}。

    下载过程中按阈值打印进度日志（每完成约 10% 或每 20 张，避免刷屏），
    让用户能看到实时进度，不至于误以为卡死。
    """
    sem = asyncio.Semaphore(max_concurrent)
    total = len(img_srcs)
    done = 0
    next_report = 0  # 下一张需要打印进度的完成数

    async def _fetch(src: str) -> Tuple[str, Optional[bytes]]:
        async with sem:
            return src, await _download_image(src, context, timeout, proxy=proxy)

    results = {}
    if img_srcs:
        tasks = [_fetch(src) for src in img_srcs]
        for coro in asyncio.as_completed(tasks):
            src, data = await coro
            results[src] = data
            done += 1
            if logger and done >= next_report:
                pct = done * 100 // total
                logger.info("🖼 图片下载进度 %d/%d (%d%%)", done, total, pct)
                # 每隔约 10% 或至少 20 张打印一次
                step = max(20, total // 10)
                next_report = min(done + step, total)
    return results


# ======================== 主下载器 ========================
class Downloader:
    """ESJZone 小说下载器 —— 编排浏览器、抓取、缓存、EPUB 全流程"""

    def __init__(self, config: dict, log: logging.Logger | None = None):
        self.config = config
        self.log = log or logger

        # 核心组件
        self.state_mgr = StateManager()
        self.cache = CacheManager()
        # 站点路由：实际抓取器在 download_novel 中按 URL 选取（见 scrapers/registry.py）
        self.scraper = DetailScraper()

        # 并发控制
        self.max_concurrent = config.get('max_concurrent', 5)
        self.image_max_concurrent = config.get('image_max_concurrent', 40)
        self.max_retries = config.get('max_retries', 3)
        self.max_overall_retries = config.get('max_overall_retries', 2)
        self.timeout = config.get('timeout', 30000)

        # 指数退避参数
        self.retry_base_delay = config.get('retry_base_delay', 1.0)
        self.retry_max_delay = config.get('retry_max_delay', 30.0)

        # 浏览器
        self._playwright = None

        # 有效图片代理：仅当"系统代理开关"开启且配置了代理地址时才使用代理；
        # 开关关闭时置为 None，_download_image 走纯直连分支，避免代理关着时
        # 每张图都经历一次代理连接超时再回退直连（每图省掉数十秒）。
        # 代理地址存在但临时不可达时，_download_image 仍会自动回退直连作为防御。
        if config.get('system_proxy', True) and config.get('image_proxy'):
            self.image_proxy = config['image_proxy']
        else:
            self.image_proxy = None
            if not config.get('system_proxy', True):
                self.log.info("🔌 系统代理已关闭，图片将使用直连")
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._image_context: Optional[BrowserContext] = None  # 图片下载专用 context

        # Page 池
        self._page_pool: asyncio.Queue = asyncio.Queue()
        self._pool_size = 0

        # 章节并发控制
        self._chapter_sem = asyncio.Semaphore(self.max_concurrent)

        # 状态批量保存（每 N 个章节写一次磁盘）
        self._save_batch_size = config.get('save_batch_size', 10)
        self._pending_save_count = 0

        # 缓存维护阈值
        self._cache_max_mb = config.get('cache_max_mb', 50)
        self._cache_ttl_hours = config.get('cache_ttl_hours', 168)

        # Page 池大小
        self._page_pool_max = config.get('page_pool_max', 8)

        # 进度追踪
        self._start_ts: float = 0.0

        # 封面 + 错误
        self._cover_bytes: Optional[bytes] = None
        self._cover_dimensions: tuple[int | None, int | None] = (None, None)
        self.error_reporter = ErrorReporter()

    # ======================== 浏览器管理 ========================
    async def _ensure_browser(self) -> None:
        """启动浏览器并恢复登录状态（或引导首次登录）"""
        if self._browser is not None:
            return

        self.log.info("启动浏览器...")
        self._playwright = await async_playwright().start()

        # 浏览器启动参数
        launch_args: list = []
        if self.config.get('browser_bypass_proxy', True):
            launch_args.append('--no-proxy-server')
            self.log.debug("🔌 浏览器已绕过系统代理（直连论坛）")

        self._browser = await self._playwright.chromium.launch(
            headless=self.config.get('headless', False),
            slow_mo=self.config.get('slow_mo', 300),
            args=launch_args,
        )

        storage_path = 'storage_state.json'
        if os.path.exists(storage_path):
            self._context = await self._browser.new_context(storage_state=storage_path)
            self.log.info("✅ 已加载登录状态")
        else:
            self.log.info("首次运行，已打开浏览器，请在浏览器中登录 ESJZone...")
            temp_context = await self._browser.new_context()
            page = await temp_context.new_page()
            await page.goto("https://www.esjzone.one/my/profile?uid=306674")
            # 自动检测登录：只有页面同时满足以下条件时才认为已登录：
            # 1. URL 仍在 profile 页面（未重定向到登录页）
            # 2. 页面上有"登出"链接（仅已登录用户可见）
            for attempt in range(100):
                await asyncio.sleep(3)
                current_url = page.url
                # 必须还在 profile 页面，不能是 login 页
                if 'login' in current_url.lower():
                    continue
                try:
                    logout_btn = page.locator('text=登出').first
                    if await logout_btn.count() > 0 and await logout_btn.is_visible():
                        self.log.info("✅ 检测到登录成功！")
                        break
                except Exception:
                    pass
                # 备选：检查用户名链接（更宽泛匹配）
                try:
                    user_link = page.locator('a[href*="/my/profile"]').first
                    if await user_link.count() > 0:
                        text = await user_link.inner_text()
                        if text.strip() and text.strip() not in ('', '个人资料', '個人資料', 'Profile'):
                            self.log.info("✅ 检测到登录成功！（用户名: %s）", text.strip()[:20])
                            break
                except Exception:
                    pass
            else:
                self.log.warning("⏰ 登录等待超时（5 分钟），将尝试继续...")
            await page.wait_for_load_state('networkidle')
            await temp_context.storage_state(path=storage_path)
            self.log.info("登录状态已保存到 %s", storage_path)
            await temp_context.close()
            self._context = await self._browser.new_context(storage_state=storage_path)

        # 图片下载用独立 context（共享 cookie）
        self._image_context = await self._browser.new_context(
            storage_state=storage_path if os.path.exists(storage_path) else None
        )

        # 初始化 Page 池
        await self._init_page_pool()

    async def _init_page_pool(self) -> None:
        """创建固定数量的 Page，放入池中复用"""
        pool_size = min(self.max_concurrent, self._page_pool_max)
        for _ in range(pool_size):
            page = await self._context.new_page()
            await self._page_pool.put(page)
        self._pool_size = pool_size
        self.log.debug("Page 池已初始化: %d 个 page", pool_size)

    async def _acquire_page(self) -> Page:
        """从池中获取一个 Page（阻塞直到可用）"""
        return await self._page_pool.get()

    async def _release_page(self, page: Page) -> None:
        """将 Page 归还池中"""
        await self._page_pool.put(page)

    async def _drain_page_pool(self) -> None:
        """排空并关闭池中所有 Page"""
        while not self._page_pool.empty():
            try:
                page = self._page_pool.get_nowait()
                await page.close()
            except asyncio.QueueEmpty:
                break

    async def close(self) -> None:
        """清理所有浏览器资源"""
        self.log.debug("正在关闭浏览器...")
        if self._browser:
            await self._drain_page_pool()
            if self._image_context:
                await self._image_context.close()
            if self._context:
                await self._context.close()
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.log.debug("浏览器已关闭")

    # ======================== 主下载流程 ========================
    async def download_novel(self, url: str, auto_yes: bool = False,
                             resume: str = ASK_MODE) -> Optional[str]:
        """
        下载一本小说的完整流程。

        参数：
            url: 小说详情页 URL
            auto_yes: 非交互模式下自动确认提示（用于自动化脚本）
            resume: 发现已有下载记录时的行为，取值见 RESUME_MODE / RESTART_MODE / ASK_MODE。
                    GUI 调用时传 RESUME_MODE 或 RESTART_MODE，避免阻塞在 input()。
        返回：
            EPUB 文件路径，失败则返回 None
        """
        novel_id = extract_novel_id(url)
        # 按 URL 路由到匹配的站点抓取器（多站点可扩展）
        self.scraper = get_scraper(url)
        if not novel_id:
            self.log.error("无效 URL，无法提取小说 ID: %s", url)
            return None

        # -- 清理旧数据 --
        if self.state_mgr.exists(novel_id):
            self.log.warning("发现已有下载记录 (ID: %s)", novel_id)
            should_restart = False
            if resume == RESTART_MODE:
                should_restart = True
            elif resume == RESUME_MODE:
                should_restart = False
            elif auto_yes:
                # 非交互模式默认清理旧数据（保持旧行为）
                should_restart = True
            else:
                choice = input("是否清理该小说的所有下载数据（状态+缓存）并重新下载？ (y/n): ").strip().lower()
                should_restart = (choice == 'y')

            if should_restart:
                self.state_mgr.delete(novel_id)
                self.cache.clear_by_novel_id(novel_id)
                self.log.info("已清理小说 %s 的所有数据，将重新下载", novel_id)
            else:
                self.log.info("继续使用现有数据（断点续传）...")

        await self._ensure_browser()

        # -- 获取/恢复状态 --
        state = self.state_mgr.load(novel_id)
        if state is None:
            state = await self._fetch_novel_info(url, novel_id)
            if state is None:
                return None  # 认证失败等
        else:
            self.log.info("📚 恢复下载: 《%s》 已完成 %d/%d 章",
                           state.title, state.completed, state.total)

        # -- 多轮下载 --
        await self._download_all(state)

        for round_num in range(1, self.max_overall_retries + 1):
            retryable = state.retryable_chapters(self.max_retries)
            if not retryable:
                break
            self.log.info("🔄 整体重试第 %d/%d 次，剩余 %d 个失败章节",
                           round_num, self.max_overall_retries, len(retryable))
            await self._download_all(state)
            await asyncio.sleep(1)

        # -- 汇总 --
        failed = [ch for ch in state.chapters.values() if ch.status == 'failed']
        skipped = [ch for ch in state.chapters.values() if ch.status == 'skipped']
        if failed:
            self.log.warning("仍有 %d 个章节下载失败（已超过重试次数）", len(failed))
            for ch in failed:
                self.log.warning("   - %s (%s)", ch.title, ch.error)
        if skipped:
            self.log.info("🔒 跳过 %d 个密码保护章节", len(skipped))

        # -- 准备 EPUB --
        epub_path = await self._prepare_and_generate_epub(state)

        # -- 最终保存 --
        self.state_mgr.save(state)
        self.error_reporter.report()

        return epub_path

    async def _fetch_novel_info(self, url: str, novel_id: str) -> Optional[NovelState]:
        """首次访问小说页，解析元信息 + 章节列表"""
        page = await self._context.new_page()
        try:
            await page.goto(url, timeout=self.timeout, wait_until='domcontentloaded')
        except Exception as e:
            self.log.error("无法访问小说页面: %s", e)
            await page.close()
            return None

        try:
            # 年龄验证弹窗
            for selector in [
                "button:has-text('确认已满18岁')",
                "button:has-text('我已满18岁')",
                "text=确认已满18岁"
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() > 0:
                        self.log.info("🔞 检测到年龄验证弹窗，正在点击确认...")
                        await btn.click()
                        await page.wait_for_load_state('networkidle', timeout=10000)
                        break
                except Exception:
                    pass

            # 检查认证
            try:
                await page.wait_for_selector('h2', timeout=10000)
            except Exception:
                current_url = page.url
                page_title = await page.title()
                self.log.error(
                    "页面加载失败 — URL: %s  标题: %s  "
                    "（可能认证过期或需要重新登录，请删除 storage_state.json 后重试）",
                    current_url[:100], page_title[:80]
                )
                self.error_reporter.add('network', f'页面加载超时 (URL={current_url[:80]})', url)
                return None

            title_text = await page.title()
            if "論壇" in title_text:
                self.error_reporter.add('auth', 'Cookie 无效或已过期', url)
                self.log.error("Cookie 无效或已过期，请删除 storage_state.json 后重新运行")
                return None

            # 解析
            info = await self.scraper.parse_novel_info(page)
            chapters = await self.scraper.parse_chapter_list(page)

            state = NovelState(
                novel_id=novel_id,
                title=info['title'],
                author=info['author'],
                alt_title=info.get('alt_title', ''),
                raw_url=info.get('raw_url', ''),
                tags=info.get('tags', []),
                total=len(chapters),
                start_time=datetime.now().isoformat()
            )
            for ch in chapters:
                state.chapters[ch['url']] = ChapterState(
                    url=ch['url'], title=ch['title'], status='pending'
                )

            # 下载封面
            if info.get('cover_url'):
                self.log.info("📷 下载封面...")
                cover_data = await _download_image(
                    info['cover_url'], self._image_context, self.timeout,
                    proxy=self.image_proxy,
                )
                if cover_data:
                    self._cover_bytes = cover_data
                    # 解析封面实际尺寸（用于生成正确的 SVG viewBox）
                    cw, ch = get_image_dimensions(cover_data)
                    if cw and ch:
                        self._cover_dimensions = (cw, ch)
                        self.log.info("   封面尺寸: %dx%d", cw, ch)
                    else:
                        self.log.warning("   未能解析封面尺寸，将使用默认 3:4 viewBox")
                else:
                    self.log.warning("封面下载失败，EPUB 将不含封面")

            self.state_mgr.save(state)
            self.log.info("📖 《%s》 共 %d 章 | 作者: %s",
                           state.title, state.total, state.author or '未知')
            return state

        finally:
            await page.close()

    # ======================== 章节下载 ========================
    async def _download_all(self, state: NovelState) -> None:
        """并发下载所有待处理章节，带进度展示和 ETA"""
        targets = [
            (url, ch_state) for url, ch_state in state.chapters.items()
            if ch_state.status in ('pending', 'failed') and ch_state.retries < self.max_retries
        ]
        if not targets:
            return

        total_targets = len(targets)
        base_completed = state.completed
        done_count = 0
        self._start_ts = time.time()
        progress_interval = max(1, total_targets // 10)  # 每 10% 报一次

        self.log.info("⬇️ 开始下载 %d 个章节 (并发: %d)", total_targets, self.max_concurrent)

        async def _wrap_one(url: str, ch_state: ChapterState):
            nonlocal done_count
            await self._download_chapter(state, url, ch_state)
            done_count += 1
            if done_count % progress_interval == 0 or done_count == total_targets:
                await self._print_progress(state, base_completed, done_count, total_targets)

        tasks = [_wrap_one(url, ch) for url, ch in targets]
        await asyncio.gather(*tasks)

        # 批量保存状态
        self.state_mgr.save(state)
        self.log.info("💾 状态已保存: %d/%d 章完成", state.completed, state.total)

    async def _print_progress(self, state: NovelState, base: int, done: int, total: int):
        """打印进度条和 ETA"""
        current = state.completed
        pct = current / state.total * 100 if state.total else 0

        elapsed = time.time() - self._start_ts
        eta_str = ""
        if current > base and elapsed > 3:
            rate = (current - base) / elapsed
            remaining = (state.total - current) / rate if rate > 0 else 0
            if remaining < 120:
                eta_str = f" 剩余约 {int(remaining)}秒"
            else:
                eta_str = f" 剩余约 {int(remaining / 60)}分{int(remaining % 60)}秒"

        bar_len = 20
        filled = int(bar_len * current / state.total) if state.total else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        self.log.info("   [%s] %d/%d (%.1f%%)%s", bar, current, state.total, pct, eta_str)

    async def _download_chapter(self, state: NovelState, url: str, ch_state: ChapterState) -> None:
        """下载单个章节（含缓存检查、指数退避重试）"""
        # 获取信号量
        async with self._chapter_sem:
            # 检查缓存
            cached = self.cache.get(url)
            if cached:
                self.log.debug("   📦 缓存命中: %s", ch_state.title)
                ch_state.status = 'completed'
                state.completed += 1
                self._bump_save(state)
                return

            # 从池中获取 page
            page = await self._acquire_page()
            try:
                for attempt in range(1, self.max_retries + 1):
                    try:
                        self.log.debug("   ⬇ [%d/%d] %s", attempt, self.max_retries, ch_state.title)
                        await page.goto(url, timeout=self.timeout, wait_until='domcontentloaded')

                        # 等待正文加载
                        try:
                            await page.wait_for_selector(
                                'div.forum-content.mt-3', timeout=15000
                            )
                        except Exception:
                            # 部分页面可能加载慢，继续尝试解析
                            pass

                        content, _ = await self.scraper.parse_chapter_content(page)

                        # 密码章节 —— 标记为跳过，不浪费重试次数
                        if '本章节需要密码' in content:
                            self.log.debug("   🔒 %s (密码章节，已跳过)", ch_state.title)
                            self.cache.set(url, content, state.novel_id, ttl_hours=self._cache_ttl_hours)
                            ch_state.status = 'skipped'
                            state.completed += 1
                            self._bump_save(state)
                            return

                        self.cache.set(url, content, state.novel_id, ttl_hours=self._cache_ttl_hours)
                        ch_state.status = 'completed'
                        state.completed += 1
                        self.log.debug("   ✅ %s", ch_state.title)
                        self._bump_save(state)
                        return

                    except Exception as e:
                        self.log.debug("   ⚠ 尝试 %d/%d 失败: %s",
                                       attempt, self.max_retries, str(e)[:80])
                        ch_state.retries = attempt
                        if attempt < self.max_retries:
                            delay = min(
                                self.retry_base_delay * (2 ** (attempt - 1)),
                                self.retry_max_delay
                            )
                            delay += random.uniform(0, delay * 0.3)  # 抖动
                            self.log.debug("   ⏳ 等待 %.1f 秒后重试...", delay)
                            await asyncio.sleep(delay)
                        else:
                            ch_state.status = 'failed'
                            ch_state.error = str(e)[:200]
                            self.error_reporter.add('chapter', str(e)[:200], url)
                            self.log.warning("   ❌ %s: %s", ch_state.title, str(e)[:80])
            finally:
                await self._release_page(page)

    def _bump_save(self, state: NovelState) -> None:
        """每 N 个章节保存一次状态（减少磁盘 I/O）"""
        self._pending_save_count += 1
        if self._pending_save_count >= self._save_batch_size:
            self.state_mgr.save(state)
            self._pending_save_count = 0

    # ======================== EPUB 准备与生成 ========================
    async def _prepare_and_generate_epub(self, state: NovelState) -> Optional[str]:
        """准备 EPUB 内容（下载图片、组装章节）并生成 EPUB"""
        if state.completed == 0:
            self.log.warning("没有成功下载任何章节")
            return None

        self.log.info("📦 准备 EPUB 内容，共 %d 章...", state.completed)
        failed_images: list = []
        chapters_content: list = []

        # 1. 收集所有需要下载的图片 URL
        all_img_urls: list = []          # 全局去重的图片列表
        chapter_img_map: list = []       # 每章的图片映射

        for url, ch_state in state.chapters.items():
            if ch_state.status not in ('completed', 'skipped'):
                continue

            content = self.cache.get(url) or '<p>内容丢失</p>'
            content = clean_html_for_epub(content)
            soup = BeautifulSoup(content, 'lxml')

            img_srcs = []
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if src and src.startswith('http'):
                    ext, _ = resolve_image_extension(src)  # URL 后缀优先
                    filename = f"image_{hashlib.md5(src.encode()).hexdigest()[:8]}{ext}"
                    img_srcs.append((src, filename))
                    img['src'] = f"images/{filename}"

            # 全角空格段落缩进（在 HTML 侧完成，不依赖 CSS text-indent）
            apply_cjk_indent_soup(soup)

            all_img_urls.extend(src for src, _ in img_srcs)
            chapter_img_map.append({
                'title': ch_state.title,
                'content': str(soup),
                'img_info': img_srcs,
            })

        # 2. 并发下载所有图片
        if all_img_urls:
            unique_urls = list(dict.fromkeys(all_img_urls))  # 去重保序
            self.log.info("🖼 下载 %d 张图片...", len(unique_urls))
            img_data_map = await _download_images_batch(
                unique_urls, self._image_context, self.timeout, self.image_max_concurrent,
                proxy=self.image_proxy, logger=self.log,
            )
        else:
            img_data_map = {}

        # 3. 组装章节内容
        for ch_map in chapter_img_map:
            content_html = ch_map['content']
            images = []
            for src, filename in ch_map['img_info']:
                data = img_data_map.get(src)
                if data:
                    # 用魔术字节重新检测真实格式（覆盖 .file 等非标准后缀）
                    real_ext, real_mime = resolve_image_extension(src, data)
                    url_ext = os.path.splitext(filename)[1]

                    if real_ext != url_ext:
                        # 扩展名不匹配 → 修正文件名 + 更新 HTML 中的 src 引用
                        base = os.path.splitext(filename)[0]
                        fixed_filename = f"{base}{real_ext}"
                        content_html = content_html.replace(
                            f'images/{filename}', f'images/{fixed_filename}'
                        )
                    else:
                        fixed_filename = filename

                    images.append({
                        'filename': fixed_filename,
                        'data': data,
                        'media_type': real_mime
                    })
                else:
                    failed_images.append({
                        'filename': f"images/{filename}",
                        'url': src
                    })

            chapters_content.append({
                'title': ch_map['title'],
                'content': content_html,
                'images': images,
            })

        # 4. 记录失败图片
        if failed_images:
            failed_txt = os.path.join(os.getcwd(), "failed_images.txt")
            with open(failed_txt, 'w', encoding='utf-8') as f:
                for item in failed_images:
                    f.write(f"{item['filename']} {item['url']}\n")
            self.log.info("📝 已记录 %d 张失败图片到 %s", len(failed_images), failed_txt)

        # 5. 生成 EPUB
        epub_path = generate_epub(
            state, chapters_content, self._cover_bytes,
            self.config.get('output_dir', './novels'),
            self.error_reporter, failed_images,
            self._cover_dimensions,
        )
        state.output_file = epub_path
        state.end_time = datetime.now().isoformat()

        # 6. 缓存维护（压缩 + 清理过期）
        db_size = self.cache.size_mb
        if db_size > self._cache_max_mb:
            self.log.info("🗜 缓存数据库 %.1fMB > %.0fMB，执行压缩/清理...", db_size, self._cache_max_mb)
            deleted = self.cache.clear_expired()
            self.log.info("   清理了 %d 条过期缓存", deleted)
            self.cache.vacuum()
            new_size = self.cache.size_mb
            self.log.info("   压缩后: %.1fMB (释放 %.1fMB)", new_size, db_size - new_size)
        else:
            self.log.debug("缓存数据库: %.1fMB / %.0fMB", db_size, self._cache_max_mb)

        return epub_path
