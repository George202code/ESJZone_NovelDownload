"""轻量级测试 wrapper：捕获所有异常和日志输出"""
import sys, traceback, asyncio, logging

# 设置日志
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)-5s] %(message)s')
_ = logging.getLogger('esjzone')

from core.downloader import Downloader
from core.state import StateManager
from core.cache import CacheManager

async def main():
    url = "https://www.esjzone.one/detail/1781790150.html"

    config = {
        "headless": False,
        "slow_mo": 100,
        "max_concurrent": 5,
        "max_retries": 3,
        "timeout": 45000,
        "output_dir": "./novels",
        "browser_bypass_proxy": True,
        "image_proxy": "http://127.0.0.1:7890",
        "max_overall_retries": 2,
        "save_batch_size": 10,
        "page_pool_max": 8,
    }

    # 全新下载：清缓存，测试章节标题抓取 + EPUB 排版
    downloader = Downloader(config)

    print("[TEST] 开始 download_novel (全新下载)...", flush=True)
    try:
        result = await downloader.download_novel(url, auto_yes=True)
        print(f"[TEST] 结果: {result}", flush=True)
    except Exception as e:
        print(f"[TEST] 异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        await downloader.close()
        print("[TEST] 完成", flush=True)

if __name__ == '__main__':
    asyncio.run(main())
