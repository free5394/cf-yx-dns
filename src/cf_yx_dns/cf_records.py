"""Cloudflare DNS 记录的读取、差量计算与同步。

本工具只管辖 A/AAAA 两类记录（见 ``docs/adr/0001-dns-record-management-scope.md``）：
查询按类型收敛，同名的其他类型记录（CNAME/TXT/MX…）既不会被读取，也不会被删除。

失败语义：CRUD 函数失败即抛异常（Cloudflare SDK 本身即抛），由同步层统一收集成
成功/失败消息 —— 不存在「返回错误字符串却被当成成功」的中间态。
"""

import time
from dataclasses import dataclass, field

from cloudflare import Cloudflare

from . import config, ips

# 本工具管辖的记录类型
MANAGED_TYPES = (ips.RECORD_TYPE_IPV4, ips.RECORD_TYPE_IPV6)
# 新建记录的 TTL（秒）
DEFAULT_TTL = 300

# Cloudflare 客户端（按 API Token 缓存，避免每次操作重复创建）
_client = None
_client_token = None


class DnsSyncError(RuntimeError):
    """同步前置条件不满足（区别于 Cloudflare API 报错）。"""


@dataclass(frozen=True)
class SyncPlan:
    """一次差量同步的计划（只读计算结果，不含任何写操作）。

    Args:
        to_create: 待新增的规范化 IP 列表
        to_delete: 待删除的现有记录列表
        proxied_by_type: 新建记录按记录类型应沿用的 proxied 值
    """

    to_create: list = field(default_factory=list)
    to_delete: list = field(default_factory=list)
    proxied_by_type: dict = field(default_factory=dict)

    @property
    def is_empty(self):
        """是否没有任何待办（记录已与优选 IP 一致）。"""
        return not self.to_create and not self.to_delete


def get_client():
    """获取按 API Token 缓存的 Cloudflare 客户端。"""
    global _client, _client_token
    token = config.cf_api_token()
    if _client is None or _client_token != token:
        _client = Cloudflare(api_token=token)
        _client_token = token
    return _client


def list_dns_records(name):
    """列出该记录名下受管辖类型（A/AAAA）的记录。

    按类型分别查询并通过页对象迭代自动翻页：翻页由 SDK 负责，判据是「还有没有下一页」，
    不依赖未在 SDK 模型中声明的 ``result_info.total_count``。

    Args:
        name: DNS 记录名

    Returns:
        list[dict]: 每条含 id/name/type/content/proxied
    """
    client = get_client()
    zone_id = config.cf_zone_id()
    records = []
    for record_type in MANAGED_TYPES:
        for record in client.dns.records.list(
            zone_id=zone_id,
            name={"exact": name},
            type=record_type,
        ):
            records.append(
                {
                    "id": record.id,
                    "name": record.name,
                    "type": record.type,
                    "content": record.content,
                    "proxied": record.proxied,
                }
            )
            print(f"现有记录: {record.type} {record.name} -> {record.content} (proxied: {record.proxied})")
    print(f"获取记录数: {len(records)}")
    return records


def create_dns_record(name, ip, *, proxied=False):
    """新建 DNS 记录。

    Args:
        name: DNS 记录名
        ip: IP 地址（写入前规范化）
        proxied: 是否走 Cloudflare 代理；显式传入，不依赖 API 的隐式默认

    Returns:
        str: 成功消息

    Raises:
        DnsSyncError: 记录名或 IP 非法
        cloudflare.APIError: Cloudflare 接口报错
    """
    addr = ips.parse_ip(ip)
    if not name or addr is None:
        raise DnsSyncError(f"新增 {name} 失败 - 记录名或 IP 非法: {ip!r}")

    canonical = str(addr)
    record_type = ips.record_type(addr)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    get_client().dns.records.create(
        zone_id=config.cf_zone_id(),
        name=name,
        content=canonical,
        ttl=DEFAULT_TTL,
        type=record_type,
        proxied=proxied,
        comment=current_time,
    )
    print(f"dns add success: ---- Time: {current_time} ---- ip:{canonical} (type: {record_type}, proxied: {proxied})")
    return f"ip:{canonical} 解析 {name} 成功"


def delete_dns_record(record_id):
    """删除 DNS 记录。

    Args:
        record_id: DNS 记录 ID

    Raises:
        DnsSyncError: record_id 为空
        cloudflare.APIError: Cloudflare 接口报错
    """
    if not record_id:
        raise DnsSyncError("删除失败 - 缺少 record_id")
    get_client().dns.records.delete(dns_record_id=record_id, zone_id=config.cf_zone_id())


def _comparison_key(record):
    """记录内容用于差量比较的键。

    以规范化 IP 身份为准，这样展开写法与压缩写法被视为同一条记录；内容不是公网 IP
    （异常数据、历史遗留）时退化为原始字符串，仍会被判为「需要删除」。
    """
    return ips.canonicalize(record["content"]) or record["content"]


def compute_diff(existing_records, target_ips):
    """计算差量。

    Args:
        existing_records: 受管辖类型下的现有记录
        target_ips: 目标 IP 列表（规范化字符串）

    Returns:
        tuple[list[str], list[dict]]: (待新增 IP 列表, 待删除记录列表)
    """
    target = list(dict.fromkeys(target_ips))
    target_keys = set(target)
    existing_keys = {_comparison_key(record) for record in existing_records}
    to_create = [ip for ip in target if ip not in existing_keys]
    to_delete = [record for record in existing_records if _comparison_key(record) not in target_keys]
    return to_create, to_delete


def _resolve_proxied(existing_records, record_type):
    """新建该类型记录时应沿用的 proxied 值。

    现有同类记录的取值一致时沿用；没有同类记录时用 False（Cloudflare 默认值）；取值
    不一致时不猜测，退回 False 并告警。
    """
    values = {bool(record["proxied"]) for record in existing_records if record["type"] == record_type}
    if len(values) == 1:
        return values.pop()
    if len(values) > 1:
        print(f"警告: 现有 {record_type} 记录的 proxied 取值不一致，新建记录使用 False")
    return False


def plan_sync(name, target_ips):
    """算出本次同步计划，不做任何写操作。

    Args:
        name: DNS 记录名
        target_ips: 目标 IP 列表（规范化字符串）

    Returns:
        SyncPlan: 差量计划

    Raises:
        DnsSyncError: 目标 IP 列表为空 —— 此时拒绝同步，绝不执行删除，
            避免上游解析异常把该域名的解析清空
    """
    if not target_ips:
        raise DnsSyncError("目标 IP 列表为空，拒绝同步（不执行任何新增或删除）")

    existing_records = list_dns_records(name)
    to_create, to_delete = compute_diff(existing_records, target_ips)

    proxied_by_type = {}
    for record_type in MANAGED_TYPES:
        if any(ips.record_type(ips.parse_ip(ip)) == record_type for ip in to_create):
            proxied_by_type[record_type] = _resolve_proxied(existing_records, record_type)

    print(f"待新增 {len(to_create)} 条，待删除 {len(to_delete)} 条")
    if not to_create and not to_delete:
        print(f"{name} 的 DNS 记录已与优选 IP 一致，无需更新")

    return SyncPlan(to_create=to_create, to_delete=to_delete, proxied_by_type=proxied_by_type)


def apply_sync(name, plan):
    """按计划执行同步：先新增缺失记录，再删除多余旧记录。

    先增后删保证同步过程中该域名始终保有可用的解析记录（见
    ``docs/adr/0002-create-before-delete-sync-order.md``）；单条失败不中断其余条目，
    也不做事务回滚。

    Args:
        name: DNS 记录名
        plan: ``plan_sync`` 算出的计划

    Returns:
        tuple[list[str], list[str]]: (成功消息列表, 失败消息列表)
    """
    success_messages = []
    error_messages = []

    for ip in plan.to_create:
        record_type = ips.record_type(ips.parse_ip(ip))
        proxied = plan.proxied_by_type.get(record_type, False)
        try:
            success_messages.append(create_dns_record(name, ip, proxied=proxied))
        except Exception as e:
            error_messages.append(f"ip:{ip} 新增 {name} 失败 - {e}")

    for record in plan.to_delete:
        try:
            delete_dns_record(record["id"])
            success_messages.append(f"ip:{record['content']} 删除旧记录成功")
        except Exception as e:
            error_messages.append(f"ip:{record['content']} 删除旧记录失败 - {e}")

    return success_messages, error_messages
