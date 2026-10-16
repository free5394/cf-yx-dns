"""优选 IP 来源的抓取、候选提取、校验与规范化。

**IP 身份以规范化字符串为准**：同一个地址无论写成压缩形式（``2606:4700::1``）还是
展开形式（``2606:4700:0:0:0:0:0:1``），解析后是同一个地址，因此 diff、去重与写回
DNS 都用同一个表示，避免同一条记录每轮被反复增删。
"""

import ipaddress
import re
import time

import httpx

from . import config

# IPv4 候选：宽松匹配，合法性由 ipaddress 兜底
_IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 方括号包裹的 IPv6 候选，例如 [2606:4700::1]#CF-IPv6_IPDB_1
_IPV6_BRACKETED_PATTERN = re.compile(r"\[([0-9a-fA-F:]+)\]")

RECORD_TYPE_IPV4 = "A"
RECORD_TYPE_IPV6 = "AAAA"


def fetch_ip_source(timeout=config.DEFAULT_TIMEOUT, max_retries=5):
    """抓取优选 IP 来源页面。

    请求失败（异常或非 200 状态码）时按指数退避（1s/2s/4s/8s）重试，最后一次失败后不再等待。

    Args:
        timeout: 单次请求超时时间（秒）
        max_retries: 最大尝试次数

    Returns:
        str | None: 页面文本，全部失败返回 None
    """
    for attempt in range(max_retries):
        try:
            response = httpx.get(config.cf_yx_url(), timeout=timeout)
            if response.status_code == 200:
                return response.text
            print(f"获取优选 IP 失败 (尝试 {attempt + 1}/{max_retries}): HTTP {response.status_code}")
        except Exception as e:
            print(f"获取优选 IP 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            time.sleep(2**attempt)
    return None


def parse_ip(text):
    """把字符串解析为 ipaddress 地址对象。

    Args:
        text: 候选字符串

    Returns:
        IPv4Address | IPv6Address | None: 非法输入返回 None
    """
    if not text:
        return None
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def record_type(addr):
    """地址对应的 DNS 记录类型。

    Args:
        addr: ipaddress 地址对象

    Returns:
        str | None: 'A'、'AAAA'，非地址对象返回 None
    """
    if isinstance(addr, ipaddress.IPv4Address):
        return RECORD_TYPE_IPV4
    if isinstance(addr, ipaddress.IPv6Address):
        return RECORD_TYPE_IPV6
    return None


def is_usable(addr):
    """地址是否可用作 DNS 记录。

    只接受**公网单播**地址：私有、回环、链路本地、保留等地址写进 DNS 必然不可用；
    组播也要显式排除（``ipaddress`` 的 ``is_global`` 并不覆盖组播段）。

    Args:
        addr: ipaddress 地址对象

    Returns:
        bool: 可用返回 True
    """
    if addr is None:
        return False
    return bool(addr.is_global and not addr.is_multicast and not addr.is_reserved and not addr.is_unspecified)


def canonicalize(text):
    """规范化一个 IP 字符串。

    非公网地址与语法非法一视同仁地返回 None（见 ``is_usable``）。

    Args:
        text: 候选字符串

    Returns:
        str | None: 规范化后的地址字符串；非法或不可用返回 None
    """
    addr = parse_ip(text)
    if not is_usable(addr):
        return None
    return str(addr)


def _clean_candidate(text):
    """清理候选字符串：去注释（``#`` 之后）、去方括号、去首尾空白。

    Returns:
        str | None: 清理结果；为空或仍含空白（说明是一整段 HTML 文本）返回 None
    """
    if not text:
        return None
    cleaned = text.strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#")[0].strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    cleaned = cleaned.strip()
    if not cleaned or " " in cleaned:
        return None
    return cleaned


def extract_candidates(text):
    """从来源文本中提取 IP 候选，保持出现顺序并去重。

    两种输入格式各自走一条明确路径：HTML 页面（IPv4 直接匹配、IPv6 取方括号内内容），
    以及纯文本列表（逗号或换行分隔，用于直接喂 IP 列表的场景）。

    Args:
        text: HTML 页面或纯文本

    Returns:
        list[str]: 候选字符串（尚未校验）
    """
    candidates = list(_IPV4_PATTERN.findall(text))
    candidates.extend(_IPV6_BRACKETED_PATTERN.findall(text))

    # 纯文本列表路径：无方括号的 IPv6 也要能识别
    if "," in text or "\n" in text:
        for item in text.replace("\n", ",").split(","):
            cleaned = _clean_candidate(item)
            if cleaned and ":" in cleaned:
                candidates.append(cleaned)

    return list(dict.fromkeys(candidates))


def parse_ip_addresses(page_text):
    """解析来源文本，返回可用的优选 IP 列表。

    非公网候选（私有/保留/回环等）与语法非法候选一并过滤，并打印跳过数量。

    Args:
        page_text: HTML 页面或纯文本

    Returns:
        list[str] | None: 规范化 IP 列表（去重保序）；无可用 IP 返回 None
    """
    if not page_text:
        print("错误: 缺少必要的参数 (page_text)")
        return None

    candidates = extract_candidates(page_text)

    usable = []
    skipped = []
    for candidate in candidates:
        canonical = canonicalize(candidate)
        if canonical is None:
            skipped.append(candidate)
        elif canonical not in usable:
            usable.append(canonical)

    if not usable:
        print("错误: 未解析到有效 IP 地址")
        return None

    if skipped:
        print(f"已跳过 {len(skipped)} 个无效或非公网候选: {skipped}")
    print(f"解析到 {len(usable)} 个有效 IP 地址")
    return usable
