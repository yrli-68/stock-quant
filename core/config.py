#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一配置加载：读取 pyproject.toml 的 [tool.stock-quant] 节"""

import logging
import os

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, 'pyproject.toml')

_cache = None


def config_path():
    return _CONFIG_PATH


def load_config(force=False):
    """读取并缓存 [tool.stock-quant] 配置；失败时返回空字典。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    try:
        with open(_CONFIG_PATH, 'rb') as f:
            data = tomllib.load(f)
        cfg = (data.get('tool') or {}).get('stock-quant') or {}
        _cache = cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logging.getLogger(__name__).debug('读取 pyproject.toml 配置失败: %s', exc)
        _cache = {}
    return _cache
