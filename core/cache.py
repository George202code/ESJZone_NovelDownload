"""SQLite 内容缓存管理"""

import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("esjzone.cache")

DEFAULT_CACHE_DB = "./cache.db"


class CacheManager:
    """基于 SQLite 的章节内容缓存，支持按小说 ID 分类管理"""

    def __init__(self, db_path: str = DEFAULT_CACHE_DB):
        self.db_path = db_path
        self._init_db()

    # ---------- 数据库初始化 ----------
    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                content    TEXT,
                novel_id   TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        # 兼容旧数据库（缺少 novel_id 列）
        cursor = conn.execute("PRAGMA table_info(cache)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'novel_id' not in columns:
            conn.execute('ALTER TABLE cache ADD COLUMN novel_id TEXT')
            logger.info("已自动升级 cache 表，添加 novel_id 列")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_novel_id ON cache(novel_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)')
        conn.commit()
        conn.close()

    # ---------- 核心操作 ----------
    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str) -> str | None:
        """获取缓存的章节内容（仅返回未过期的）"""
        key = self._key(url)
        conn = self._get_conn()
        row = conn.execute(
            'SELECT content FROM cache '
            'WHERE key=? AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)',
            (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def set(self, url: str, content: str, novel_id: str, ttl_hours: int = 168) -> None:
        """缓存章节内容，默认 TTL 7 天"""
        key = self._key(url)
        expires_ts = datetime.now().timestamp() + ttl_hours * 3600
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, content, novel_id, expires_at) "
            "VALUES (?, ?, ?, datetime(?, 'unixepoch'))",
            (key, content, novel_id, expires_ts)
        )
        conn.commit()
        conn.close()

    def clear_by_novel_id(self, novel_id: str) -> None:
        """清理指定小说的全部缓存"""
        conn = self._get_conn()
        conn.execute('DELETE FROM cache WHERE novel_id = ?', (novel_id,))
        conn.commit()
        conn.close()
        logger.info("已清理小说 %s 的缓存", novel_id)

    def clear_all(self) -> None:
        """清空全部缓存"""
        conn = self._get_conn()
        conn.execute('DELETE FROM cache')
        conn.commit()
        conn.close()
        logger.info("已清空全部缓存")

    def clear_expired(self) -> int:
        """清理过期缓存，返回清理条数"""
        conn = self._get_conn()
        cursor = conn.execute(
            'DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP'
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info("清理了 %d 条过期缓存", deleted)
        return deleted

    def vacuum(self) -> None:
        """压缩数据库文件，释放磁盘空间"""
        conn = self._get_conn()
        conn.execute('VACUUM')
        conn.close()
        logger.info("已完成数据库压缩")

    @property
    def size_mb(self) -> float:
        """返回数据库文件大小（MB）"""
        return Path(self.db_path).stat().st_size / (1024 * 1024) if Path(self.db_path).exists() else 0

    def stats(self) -> dict:
        """返回缓存统计信息"""
        conn = self._get_conn()
        total = conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
        expired = conn.execute(
            'SELECT COUNT(*) FROM cache WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP'
        ).fetchone()[0]
        # 按小说分组统计
        novel_counts = conn.execute(
            'SELECT novel_id, COUNT(*) as cnt FROM cache GROUP BY novel_id ORDER BY cnt DESC'
        ).fetchall()
        conn.close()
        return {
            'total_entries': total,
            'expired_entries': expired,
            'valid_entries': total - expired,
            'db_size_mb': self.size_mb,
            'novel_groups': len(novel_counts),
            'top_novels': novel_counts[:5],
        }
