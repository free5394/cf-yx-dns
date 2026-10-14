#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare DNS 更新器
获取优选 IP 并更新 Cloudflare DNS 记录
"""

import argparse
import ipaddress
import re
import sys
import time
import os

import httpx
from cloudflare import Cloudflare
from dotenv import load_dotenv

load_dotenv()

# API 配置
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
# 优选 IP URL
CF_YX_URL = os.environ.get("CF_YX_URL") or "https://ip.164746.xyz/ipTop.html"
# pushplus_token
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 30

# Cloudflare 客户端（惰性单例，避免每次操作重复创建）
_client = None


def _get_client():
    """
    获取 Cloudflare 客户端
    """
    global _client
    if _client is None:
        _client = Cloudflare(api_token=CF_API_TOKEN)
    return _client


def get_cf_speed_test_ip(timeout=10, max_retries=5):
    """
    获取 Cloudflare 优选 IP

    请求失败（异常或非 200 状态码）时按指数退避重试。

    Args:
        timeout: 单次请求超时时间
        max_retries: 最大重试次数

    Returns:
        优选 IP 字符串，失败返回 None
    """
    for attempt in range(max_retries):
        try:
            response = httpx.get(
                CF_YX_URL,
                timeout=timeout
            )
            if response.status_code == 200:
                return response.text
            print(f"获取优选 IP 失败 (尝试 {attempt + 1}/{max_retries}): HTTP {response.status_code}")
        except Exception as e:
            print(f"获取优选 IP 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
        # 指数退避: 1s, 2s, 4s, 8s，最后一次失败后不再等待
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return None

def _is_valid_ip(ip_str):
    """
    验证单个 IP 地址是否为有效的 IPv4 或 IPv6 地址

    Args:
        ip_str: IP 地址字符串

    Returns:
        bool: 是否为有效 IP 地址
    """
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def _get_ip_type(ip_str):
    """
    获取 IP 地址类型

    Args:
        ip_str: IP 地址字符串

    Returns:
        str: 'A' 表示 IPv4，'AAAA' 表示 IPv6，None 表示无效
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if isinstance(ip, ipaddress.IPv4Address):
            return 'A'
        elif isinstance(ip, ipaddress.IPv6Address):
            return 'AAAA'
    except ValueError:
        pass
    return None


def _extract_potential_ips(text):
    """
    从文本中提取可能的 IP 地址候选（包括 HTML 页面）

    支持 IPv4 和 IPv6 格式识别。

    Args:
        text: 任意文本字符串（可以是纯 IP 列表或 HTML 页面）

    Returns:
        list: IP 地址候选列表
    """
    candidates = []

    # IPv4 正则（宽松匹配）
    ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    candidates.extend(re.findall(ipv4_pattern, text))

    # IPv6 提取：处理带方括号和注释的格式
    # 例如: [2606:4700:0:9623:8f54:b235:2eb0:82a0]#CF-IPv6_IPDB_1
    # 或: [2606:4700::2df7:23e0:8908:4752]#CF-IPv6_IPDB_2
    ipv6_bracketed = r'\[([0-9a-fA-F:]+)\]'
    bracketed_matches = re.findall(ipv6_bracketed, text)
    candidates.extend(bracketed_matches)

    # 同时处理带冒号但可能没有方括号的 IPv6（向后兼容）
    if ',' in text or '\n' in text:
        normalized = text.replace('\n', ',')
        split_candidates = [x.strip() for x in normalized.split(',') if x.strip()]
        for candidate in split_candidates:
            if ':' in candidate and candidate not in candidates:
                # 清理 IPv6 地址：去除方括号和注释
                cleaned = _clean_ip_address(candidate)
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)

    # 去重但保持顺序
    seen = set()
    unique_candidates = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)

    return unique_candidates


def _clean_ip_address(ip_str):
    """
    清理 IP 地址字符串，去除方括号、注释等杂质

    Args:
        ip_str: 原始 IP 字符串

    Returns:
        str: 清理后的 IP 地址，无效返回 None
    """
    if not ip_str:
        return None

    cleaned = ip_str.strip()

    # 先去除注释（# 及其后面的内容），因为注释可能在方括号外
    if '#' in cleaned:
        cleaned = cleaned.split('#')[0].strip()

    # 再去除方括号
    if cleaned.startswith('[') and cleaned.endswith(']'):
        cleaned = cleaned[1:-1]

    # 再次去除首尾空白
    cleaned = cleaned.strip()

    # 如果清理后为空或包含空白，则无效
    if not cleaned or ' ' in cleaned:
        return None

    return cleaned


def parse_ip_addresses(ip_str):
    """
    解析优选 IP 字符串或 HTML 页面，提取有效 IP 地址

    支持多种输入格式：
    - 纯 IP 列表（逗号/换行分隔）
    - 包含 IP 的 HTML 页面

    Args:
        ip_str: IP 字符串或 HTML 页面内容

    Returns:
        list: 有效 IP 地址列表（已去重），失败返回 None
    """
    if not ip_str:
        print("错误: 缺少必要的参数 (ip_str)")
        return None

    potential_ips = _extract_potential_ips(ip_str)

    if not potential_ips:
        print("错误: 未解析到有效 IP 地址")
        return None

    valid_ips = []
    invalid_ips = []

    for ip in potential_ips:
        if _is_valid_ip(ip):
            valid_ips.append(ip)
        else:
            invalid_ips.append(ip)

    if not valid_ips:
        print("错误: 未解析到有效 IP 地址")
        return None

    # 候选已在 _extract_potential_ips 中去重，此处无需再次去重
    print(f"解析到 {len(valid_ips)} 个有效 IP 地址")
    return valid_ips

def get_dns_records(name):
    """
    获取指定名称的 DNS 记录列表

    Args:
        name: DNS 记录名称

    Returns:
        记录字典列表（包含 id, name, type, content, comment），失败返回空列表
    """
    client = _get_client()
    current_page = 0
    current_count = 0
    total_count = 0
    records = []
    while current_page == 0 or current_count < total_count:
        current_page += 1
        page = client.dns.records.list(
            zone_id=CF_ZONE_ID,
            name=name,
            page=current_page,
            per_page=20,
        )
        if not page or not page.result or not page.result_info:
            break

        for record in page.result:
            records.append({
                'id': record.id,
                'name': record.name,
                'type': record.type,
                'content': record.content,
                'comment': record.comment,
            })
        # 当前获取的总记录数
        current_count += len(page.result)
        # 总记录数
        total_count = page.result_info.total_count
        print(f"总记录数: {total_count}，当前记录数: {current_count}")
    
    print(f"获取记录数: {len(records)}")
    return records

def add_dns_record(name, cf_ip):
    """
    创建 DNS 记录

    Args:
        name: DNS 记录名称
        cf_ip: 新的 IP 地址

    Returns:
        操作结果字符串
    """
    if not name or not cf_ip:
        print("错误: 缺少必要的参数 (name, cf_ip)")
        return f"ip:{cf_ip} 新增 {name} 失败 - 缺少参数"

    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    client = _get_client()

    ip_type = _get_ip_type(cf_ip)
    if not ip_type:
        print(f"错误: 无效的 IP 地址: {cf_ip}")
        return f"ip:{cf_ip} 解析 {name} 失败 - 无效 IP 地址"

    client.dns.records.create(
        zone_id=CF_ZONE_ID,
        name=name,
        content=cf_ip,
        ttl=300,
        type=ip_type,
        comment=f"{current_time}",
    )
    # SDK 在 API 错误时抛出 cloudflare.APIError，由调用方（sync_dns_records）统一捕获
    print(f"dns add success: ---- Time: {current_time} ---- ip:{cf_ip}")
    return f"ip:{cf_ip} 解析 {name} 成功"

def del_dns_record(record_id):
    """
    删除 DNS 记录

    Args:
        record_id: DNS 记录 ID

    Returns:
        操作结果字符串
    """
    if not record_id:
        print("错误: 缺少必要的参数 (record_id)")
        return

    client = _get_client()
    record = client.dns.records.delete(
        dns_record_id=record_id,
        zone_id=CF_ZONE_ID,
    )
    return record

def _compute_diff(existing_records, new_ips):
    """
    计算差量：需要新增的 IP 与需要删除的记录

    Args:
        existing_records: 现有 DNS 记录列表
        new_ips: 目标 IP 地址列表

    Returns:
        tuple: (待新增 IP 列表, 待删除记录列表)
    """
    existing_contents = {record['content'] for record in existing_records}
    target_ips = list(dict.fromkeys(new_ips))
    to_create = [ip for ip in target_ips if ip not in existing_contents]
    to_delete = [record for record in existing_records if record['content'] not in target_ips]
    return to_create, to_delete

def sync_dns_records(name, new_ips):
    """
    差量同步 DNS 记录：先新增缺失的记录，再删除多余的旧记录

    采用"先增后删"的顺序，保证同步过程中该域名始终保有可用的解析记录，
    避免因中途失败导致解析中断。

    Args:
        name: DNS 记录名称
        new_ips: 目标 IP 地址列表

    Returns:
        tuple: (成功消息列表, 失败消息列表)
    """
    try:
        existing_records = get_dns_records(name) or []
    except Exception as e:
        print(f"获取现有 DNS 记录失败: {e}", file=sys.stderr)
        return [], [f"获取 {name} 现有 DNS 记录失败 - {e}"]
    to_create, to_delete = _compute_diff(existing_records, new_ips)

    print(f"待新增 {len(to_create)} 条，待删除 {len(to_delete)} 条")

    success_messages = []
    error_messages = []

    # 先新增，确保任意时刻都存在可用记录
    for ip in to_create:
        try:
            success_messages.append(add_dns_record(name, ip))
        except Exception as e:
            error_messages.append(f"ip:{ip} 新增 {name} 失败 - {e}")

    # 再删除多余的旧记录
    for record in to_delete:
        try:
            del_dns_record(record['id'])
            success_messages.append(f"ip:{record['content']} 删除旧记录成功")
        except Exception as e:
            error_messages.append(f"ip:{record['content']} 删除旧记录失败 - {e}")

    return success_messages, error_messages

def push_plus(content):
    """
    发送 PushPlus 消息推送

    Args:
        content: 消息内容
    """
    if not PUSHPLUS_TOKEN:
        print("PUSHPLUS_TOKEN 未设置，跳过消息推送")
        return

    url = 'https://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": "IP优选DNSCF推送",
        "content": content,
        "template": "markdown",
        "channel": "wechat"
    }

    try:
        httpx.post(url, json=data, timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        print(f"消息推送失败: {e}")

def main(argv=None) -> int:
    """主函数

    Args:
        argv: 命令行参数列表，None 时读取 sys.argv

    Returns:
        int: 0 表示全部成功，1 表示存在失败
    """
    parser = argparse.ArgumentParser(
        description="Cloudflare DNS 优选 IP 自动更新工具"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：仅显示将要新增/删除的记录，不实际修改 DNS",
    )
    args = parser.parse_args(argv)

    # 检查必要的环境变量
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("错误: 缺少必要的环境变量 (CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME)", file=sys.stderr)
        return 1

    # 获取最新优选 IP
    ip_addresses_str = get_cf_speed_test_ip()
    if not ip_addresses_str:
        print("错误: 无法获取优选 IP", file=sys.stderr)
        return 1

    ip_addresses = parse_ip_addresses(ip_addresses_str)
    if not ip_addresses:
        print("错误: 未解析到有效 IP 地址", file=sys.stderr)
        return 1

    for index, ip_address in enumerate(ip_addresses):
        print(f"{index}: {ip_address}")

    # 演练模式：仅展示差量，不实际修改 DNS 记录
    if args.dry_run:
        try:
            existing_records = get_dns_records(CF_DNS_NAME) or []
        except Exception as e:
            print(f"获取现有 DNS 记录失败: {e}", file=sys.stderr)
            return 1
        to_create, to_delete = _compute_diff(existing_records, ip_addresses)
        print(f"[演练模式] 将新增 {len(to_create)} 条: {to_create}")
        print(f"[演练模式] 将删除 {len(to_delete)} 条: {[r['content'] for r in to_delete]}")
        return 0

    # 差量同步 DNS 记录（先新增缺失记录，再删除多余旧记录）
    success_messages, error_messages = sync_dns_records(CF_DNS_NAME, ip_addresses)

    # 发送推送
    push_plus_content = success_messages + [f"失败: {message}" for message in error_messages]
    if push_plus_content:
        print(push_plus_content)
        push_plus('\n'.join(push_plus_content))

    if error_messages:
        print(f"错误: 存在 {len(error_messages)} 项 DNS 操作失败", file=sys.stderr)
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
