"""小说下载状态管理"""

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, List

logger = logging.getLogger("esjzone.state")

STATE_DIR = "./state"
os.makedirs(STATE_DIR, exist_ok=True)


@dataclass
class ChapterState:
    """单章下载状态"""
    url: str
    title: str
    status: str = "pending"  # pending, downloading, completed, failed, skipped
    retries: int = 0
    error: str = ""


@dataclass
class NovelState:
    """整本小说下载状态"""
    novel_id: str
    title: str
    author: str = ""
    alt_title: str = ""
    raw_url: str = ""
    tags: List[str] = field(default_factory=list)
    chapters: Dict[str, ChapterState] = field(default_factory=dict)
    completed: int = 0
    total: int = 0
    cover_path: str = ""
    start_time: str = ""
    end_time: str = ""
    output_file: str = ""

    def failed_count(self) -> int:
        """统计失败章节数"""
        return sum(1 for ch in self.chapters.values() if ch.status == 'failed')

    def retryable_chapters(self, max_retries: int) -> list:
        """返回可重试的章节列表"""
        return [
            ch for ch in self.chapters.values()
            if ch.status in ('pending', 'failed') and ch.retries < max_retries
        ]


class StateManager:
    """小说下载状态的持久化管理器"""

    def __init__(self, state_dir: str = STATE_DIR):
        self.state_dir = state_dir

    def _path(self, novel_id: str) -> str:
        return os.path.join(self.state_dir, f"{novel_id}.json")

    def exists(self, novel_id: str) -> bool:
        return os.path.exists(self._path(novel_id))

    def load(self, novel_id: str) -> Optional[NovelState]:
        path = self._path(novel_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        state = NovelState(
            novel_id=data['novel_id'],
            title=data['title'],
            author=data.get('author', ''),
            alt_title=data.get('alt_title', ''),
            raw_url=data.get('raw_url', ''),
            tags=data.get('tags', []),
            total=data['total'],
            completed=data['completed'],
            cover_path=data.get('cover_path', ''),
            start_time=data.get('start_time', ''),
            end_time=data.get('end_time', ''),
            output_file=data.get('output_file', '')
        )
        for url, ch_data in data.get('chapters', {}).items():
            state.chapters[url] = ChapterState(**ch_data)
        logger.debug("加载状态: %s (%d/%d 章)", state.title, state.completed, state.total)
        return state

    def save(self, state: NovelState) -> None:
        data = {
            'novel_id': state.novel_id,
            'title': state.title,
            'author': state.author,
            'alt_title': state.alt_title,
            'raw_url': state.raw_url,
            'tags': state.tags,
            'total': state.total,
            'completed': state.completed,
            'cover_path': state.cover_path,
            'start_time': state.start_time,
            'end_time': state.end_time,
            'output_file': state.output_file,
            'chapters': {url: asdict(ch) for url, ch in state.chapters.items()}
        }
        with open(self._path(state.novel_id), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def delete(self, novel_id: str) -> bool:
        path = self._path(novel_id)
        if os.path.exists(path):
            os.remove(path)
            logger.info("已删除状态: %s", novel_id)
            return True
        return False
