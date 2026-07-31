"""ESJZone 小说下载器 —— CLI 入口"""

import argparse
import asyncio
import logging
import os
import sys

from core.cache import CacheManager
from core.downloader import Downloader
from core.state import StateManager
from core.config import load_config  # 统一配置入口（含默认值兜底，config.json 缺失也不崩溃）
from scrapers.registry import available_sites
from utils.helpers import setup_logging, extract_novel_id


async def cmd_maintain(log: logging.Logger) -> None:
    """维护模式：清理过期缓存 + 数据库压缩 + 缓存统计"""
    cache = CacheManager()
    state_dir = StateManager().state_dir

    # 缓存统计
    stats = cache.stats()
    log.info("📊 缓存统计: %d 条有效 / %d 条过期 / %.1f MB / %d 本小说",
             stats['valid_entries'], stats['expired_entries'],
             stats['db_size_mb'], stats['novel_groups'])

    # 清理过期
    deleted = cache.clear_expired()
    if deleted > 0:
        log.info("🗑 清理了 %d 条过期缓存", deleted)

    # 压缩
    old_size = stats['db_size_mb']
    if deleted > 0 or old_size > 10:
        cache.vacuum()
        new_size = cache.size_mb
        log.info("🗜 压缩后: %.1f MB (释放 %.1f MB)", new_size, old_size - new_size)

    # 统计状态文件
    if os.path.isdir(state_dir):
        state_files = [f for f in os.listdir(state_dir) if f.endswith('.json')]
        log.info("📁 状态目录: %d 个下载记录", len(state_files))

    log.info("✅ 维护完成")


async def main_async(args: argparse.Namespace, config: dict, log: logging.Logger) -> None:
    """主异步入口"""

    # --- 维护模式 ---
    if args.maintain:
        await cmd_maintain(log)
        return

    # 覆盖配置
    config['headless'] = args.headless
    config['max_concurrent'] = args.concurrent or config.get('max_concurrent', 5)
    config['max_retries'] = args.retries or config.get('max_retries', 5)
    if args.output:
        config['output_dir'] = args.output
    if args.image_proxy is not None:
        config['image_proxy'] = args.image_proxy  # 空字符串 = 禁用代理

    downloader = Downloader(config, log)

    try:
        if args.clear_cache:
            novel_id = extract_novel_id(args.url) if args.url else None
            if novel_id:
                downloader.cache.clear_by_novel_id(novel_id)
                downloader.state_mgr.delete(novel_id)
                log.info("已清理小说 %s 的缓存和状态", novel_id)
            else:
                log.info("无法从 URL 提取小说 ID，正在清空全部缓存...")
                downloader.cache.clear_all()
            return

        if not args.url:
            log.error("请提供小说 URL，或使用 --maintain 执行维护")
            sys.exit(1)

        epub_path = await downloader.download_novel(
            args.url, auto_yes=args.yes, resume=args.resume)
        if epub_path:
            log.info("🎉 下载完成！EPUB 已保存到: %s", epub_path)
        else:
            sys.exit(1)

    finally:
        await downloader.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESJZone 小说下载器 —— 将 esjzone.one 轻小说下载为 EPUB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 基本用法
  python main.py "https://www.esjzone.one/detail/xxx.html"

  # 日常最优命令（无头 + 适中并发 + 高重试）
  python main.py -H -c 5 -r 5 "https://www.esjzone.one/detail/xxx.html"

  # 长篇推荐（更保守的并发）
  python main.py -H -c 5 -r 5 -v "https://www.esjzone.one/detail/xxx.html"

  # 清理指定小说 + 重新下载
  python main.py --clear-cache "https://www.esjzone.one/detail/xxx.html"

  # 缓存维护（定期执行，建议每 5-10 本书后）
  python main.py --maintain
        """
    )
    parser.add_argument(
        'url', nargs='?',
        help='小说详情页 URL'
    )
    parser.add_argument(
        '-H', '--headless', action='store_true',
        help='无头模式运行（不显示浏览器窗口）'
    )
    parser.add_argument(
        '-c', '--concurrent', type=int,
        help='最大并发下载数 (默认 5)'
    )
    parser.add_argument(
        '-r', '--retries', type=int,
        help='单章最大重试次数 (默认 5)'
    )
    parser.add_argument(
        '-o', '--output', type=str,
        help='EPUB 输出目录 (默认 ./novels)'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='详细日志输出 (DEBUG 级别)'
    )
    parser.add_argument(
        '--clear-cache', action='store_true',
        help='清理指定小说的缓存和状态后退出'
    )
    parser.add_argument(
        '--maintain', action='store_true',
        help='缓存维护模式：清理过期 + 压缩数据库 + 统计'
    )
    parser.add_argument(
        '-y', '--yes', action='store_true',
        help='自动确认所有交互提示（用于自动化脚本）'
    )
    parser.add_argument(
        '--image-proxy', type=str, default=None,
        help='图片下载代理地址 (如 http://127.0.0.1:7890)，用于加速插图加载'
    )
    parser.add_argument(
        '--list-sites', action='store_true',
        help='列出已注册的小说站点（多站点扩展调试用）并退出'
    )
    parser.add_argument(
        '--no-proxy', action='store_true',
        help='关闭系统代理，图片使用直连（代理未启动时必须加，否则每张图会先等代理超时再回退）'
    )
    parser.add_argument(
        '--resume', choices=['resume', 'restart', 'ask'], default='ask',
        help='发现已有下载记录时的行为: resume=断点续传, restart=清理旧数据重下, ask=交互询问(默认)'
    )
    parser.add_argument(
        '--image-concurrent', type=int, default=None,
        help='图片下载并发数（默认取 config.image_max_concurrent，可高于章节并发）'
    )

    args = parser.parse_args()

    if args.list_sites:
        print("已注册站点:", ", ".join(available_sites()))
        return

    config = load_config()
    # 命令行 --no-proxy 覆盖配置文件的 system_proxy 开关
    if args.no_proxy:
        config['system_proxy'] = False
    # 命令行 --image-concurrent 覆盖图片下载并发数（与章节并发解耦）
    if args.image_concurrent:
        config['image_max_concurrent'] = args.image_concurrent
    log = setup_logging(verbose=args.verbose)

    try:
        asyncio.run(main_async(args, config, log))
    except KeyboardInterrupt:
        log.warning("用户中断 (Ctrl+C)")


if __name__ == '__main__':
    main()
