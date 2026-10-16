"""运行期配置：环境变量的唯一读取点。

环境变量在调用时读取，而不是在 import 时固化：测试可以用 monkeypatch 控制，
运行中改环境也能生效。``.env`` 的加载由 CLI 入口显式完成（见 ``cli.main``）。
"""

import os

# 优选 IP 来源地址（可用环境变量 CF_YX_URL 覆盖）
DEFAULT_CF_YX_URL = "https://ip.164746.xyz/ipTop.html"

# HTTP 默认超时（秒）
DEFAULT_TIMEOUT = 30


def cf_api_token():
    """Cloudflare API Token。"""
    return os.environ.get("CF_API_TOKEN")


def cf_zone_id():
    """Cloudflare Zone ID。"""
    return os.environ.get("CF_ZONE_ID")


def cf_dns_name():
    """本工具管辖的 DNS 记录名。"""
    return os.environ.get("CF_DNS_NAME")


def cf_yx_url():
    """优选 IP 来源地址。"""
    return os.environ.get("CF_YX_URL") or DEFAULT_CF_YX_URL


def pushplus_token():
    """PushPlus Token；未设置（或为空）表示跳过推送。"""
    return os.environ.get("PUSHPLUS_TOKEN")
