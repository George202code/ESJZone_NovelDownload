"""站点抓取器注册表。

多站点扩展的核心路由：downloader 不再硬编码 DetailScraper，而是按 URL 选取匹配站点。
新增站点步骤：
  1. 在 scrapers/ 下新建 xxx.py，定义 class XxxScraper(BaseScraper) 并实现四个抽象方法
  2. 在此文件 SCRAPERS 列表追加 XxxScraper() 实例
downloader / GUI 均无需改动。
"""
from __future__ import annotations

from typing import List

from core.scraper import BaseScraper
from scrapers.detail import DetailScraper


# 已注册站点（顺序即匹配优先级）
SCRAPERS: List[BaseScraper] = [
    DetailScraper(),  # ESJZone (esjzone.one)
    # 未来新增：NovelSiteScraper(), ...
]


def get_scraper(url: str) -> BaseScraper:
    """根据 URL 返回第一个 can_handle 为 True 的抓取器。

    约定：can_handle 应只匹配本站点域名，避免误判。
    """
    for scraper in SCRAPERS:
        try:
            if scraper.can_handle(url):
                return scraper
        except Exception:
            # 单个 scraper 的匹配异常不应阻断其他站点
            continue
    raise ValueError(f"无匹配抓取器: {url}")


def available_sites() -> List[str]:
    """返回已注册站点的可读名称，供 GUI 下拉框使用。"""
    return [type(s).__name__.replace("Scraper", "") for s in SCRAPERS]
