# B 方案：图片下载并发与章节并发解耦

> 状态：待执行（节后）
> 关联任务：1781790150 提速（A 方案已于 2026-08-01 执行，用 `-c 20 --resume resume --no-proxy` 重跑）

## 背景

A 方案实测：794 张图片用 `max_concurrent=20` 直连，吞吐仅 0.8 张/秒（单图约 16s，CDN 直连延迟硬伤）。
当前 `core/downloader.py` 中**图片下载与章节下载共用 `self.max_concurrent`**：

- 章节下载：`_download_all()` 用 `self._chapter_sem = asyncio.Semaphore(self.max_concurrent)`（L185）
- 图片下载：`_download_images_batch(unique_urls, ..., self.max_concurrent, ...)`（L672）→ 内部 `sem = asyncio.Semaphore(max_concurrent)`（L114）

拉高 `max_concurrent` 会同时抬高章节浏览器并发，章节需浏览器渲染 + 登录态，不宜过高（5~10 安全）。

## 目标

图片（纯 I/O、直连 httpx）可安全用更高并发（如 40），与章节解耦，互不影响稳定性。

## 改动清单

### 1. `core/config.py` — 新增默认值
```python
DEFAULT_CONFIG = {
    ...
    "max_concurrent": 5,        # 章节并发（浏览器渲染，保守）
    "image_max_concurrent": 40, # 图片并发（纯 I/O 直连，可激进）
}
```

### 2. `core/downloader.py` — 读独立配置
- `__init__` 新增 `self.image_max_concurrent = config.get('image_max_concurrent', 40)`
- L672 调用处改为传 `self.image_max_concurrent`：
  ```python
  img_data_map = await _download_images_batch(
      unique_urls, self._image_context, self.timeout, self.image_max_concurrent,
      proxy=self.image_proxy, logger=self.log,
  )
  ```

### 3. `core/downloader.py` — `_download_images_batch` 签名无需改
已接收 `max_concurrent` 参数，调用方传不同值即可。

### 4. `main.py` — 新增 CLI 参数（可选）
```python
parser.add_argument('--image-concurrent', type=int, default=None,
    help='图片下载并发数（默认取 config.image_max_concurrent）')
```
解析后 `if args.image_concurrent: config['image_max_concurrent'] = args.image_concurrent`

### 5. `README.md` / `CHANGELOG.md` — 文档更新
- README 配置示例补 `image_max_concurrent`；参数表补 `--image-concurrent`
- CHANGELOG 新增 v2.6：图片并发解耦

## 验证

```bash
# 解耦后图片并发应显著高于章节
python main.py "https://www.esjzone.one/detail/1781790150.html" \
    --no-proxy --resume resume --image-concurrent 40
```
观察 `_download_images_batch` 进度日志速率是否 > 0.8 张/秒（预期 1.5~2 张/秒）。

## 风险

- CDN 可能对单客户端限流，并发过高收益递减（实测 20 并发已占满带宽/连接）
- 极端并发可能触发 ESJZone 反爬 → 需配合现有指数退避重试（已具备）
- `image_max_concurrent` 建议上限 50，避免打满本地端口
