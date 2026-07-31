"""抓取器抽象基类"""

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Any


class BaseScraper(ABC):
    """所有网站抓取器的抽象基类"""

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """检查是否能处理此 URL"""
        ...

    @abstractmethod
    async def parse_novel_info(self, page) -> Dict[str, Any]:
        """解析小说元信息（标题、作者、封面等）"""
        ...

    @abstractmethod
    async def parse_chapter_list(self, page) -> List[Dict[str, str]]:
        """解析章节列表，返回 [{"title": "", "url": ""}, ...]"""
        ...

    @abstractmethod
    async def parse_chapter_content(self, page) -> Tuple[str, List[str]]:
        """解析章节正文，返回 (html_content, [image_urls])"""
        ...
