"""统一配置加载。

项目存在 config.json（代理/并发/超时等），但 core 各模块此前各自硬编码 dict。
新增 load_config() 作为唯一入口，cli.py / GUI / _test_run.py 均应通过它读取配置。
"""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

# 配置默认值（与 Downloader.__init__ 的 fallback 保持一致，作为 config.json 缺失时的兜底）
DEFAULT_CONFIG: dict[str, Any] = {
    "image_proxy": "http://127.0.0.1:7890",
    "system_proxy": True,  # 系统代理总开关：False 时图片纯直连，不尝试代理（避免代理关着每图超时回退）
    "browser_bypass_proxy": True,
    "headless": False,
    "slow_mo": 300,
    "max_concurrent": 5,
    "image_max_concurrent": 40,  # 图片下载并发（纯 I/O 直连，可高于章节并发）
    "max_retries": 3,
    "max_overall_retries": 2,
    "timeout": 30000,
    "retry_base_delay": 1.0,
    "retry_max_delay": 30.0,
    "page_pool_max": 8,
    "cache_max_mb": 50,
    "cache_ttl_hours": 168,
    "save_batch_size": 10,
    "output_dir": "./novels",
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """读取 config.json，与其余默认值合并。

    优先级：config.json 中的显式值 > DEFAULT_CONFIG 兜底值。
    config.json 不存在时返回完整默认值（不影响运行）。
    """
    path = path or DEFAULT_CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            cfg.update({k: v for k, v in user_cfg.items() if k in DEFAULT_CONFIG})
        except (json.JSONDecodeError, OSError) as e:
            # 配置损坏不应中断程序，回退到默认值并提示
            print(f"[config] 读取 {path} 失败，使用默认配置: {e}")
    return cfg


def save_config(cfg: dict[str, Any], path: str | None = None) -> None:
    """将配置写回 config.json（仅保存已知键）。GUI 设置面板会用到。"""
    path = path or DEFAULT_CONFIG_PATH
    out = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
