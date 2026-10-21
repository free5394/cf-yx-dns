"""Cloudflare DNS 记录的差量计算与同步。

本工具只管辖 A/AAAA 两类记录（见 ``docs/adr/0001-dns-record-management-scope.md``）：
查询按类型收敛，同名的其他类型记录（CNAME/TXT/MX…）既不会被读取，也不会被删除。

两个同步动词（``plan_sync`` / ``apply_sync``）把**记录存储**作为显式依赖接收，自己不再
创建 Cloudflare 客户端：它们只认识「列出记录名下的管辖记录、新建一条记录、删除一条记录」
这三个业务操作与 ``ManagedRecord`` 值类型，不认识 SDK、Zone、Token 与 TTL。谁接触网络由
传入的实现决定 —— Cloudflare 实现在 ``cf_record_store``，测试注入替身即可。

失败语义：记录存储的写操作失败即抛异常，由 ``apply_sync`` 统一收集成成功/失败消息 ——
不存在「返回错误字符串却被当成成功」的中间态。
"""

from dataclasses import dataclass, field

from . import ips

# 本工具管辖的记录类型
MANAGED_TYPES = (ips.RECORD_TYPE_IPV4, ips.RECORD_TYPE_IPV6)


class DnsSyncError(RuntimeError):
    """同步前置条件不满足（区别于 Cloudflare API 报错）。"""


@dataclass(frozen=True)
class ManagedRecord:
    """一条受管辖的现有 DNS 记录。

    Args:
        id: Cloudflare 记录 ID
        name: 记录名
        type: 记录类型（A/AAAA）
        content: 记录内容（IP 字符串）
        proxied: 是否走 Cloudflare 代理
    """

    id: str
    name: str
    type: str
    content: str
    proxied: bool


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


def _canonical_create_target(name, ip):
    """把一条待新增的 IP 校验并规范化成记录存储要写入的字段。

    校验与规范化属于同步层：记录存储的实现只管写入，不必各自重复一次业务判断。

    Args:
        name: DNS 记录名
        ip: IP 地址（写入前规范化）

    Returns:
        tuple[str, str]: (记录类型, 规范化 IP)

    Raises:
        DnsSyncError: 记录名或 IP 非法
    """
    addr = ips.parse_ip(ip)
    if not name or addr is None:
        raise DnsSyncError(f"记录名或 IP 非法: {ip!r}")
    return ips.record_type(addr), str(addr)


def _comparison_key(record):
    """记录内容用于差量比较的键。

    以规范化 IP 身份为准，这样展开写法与压缩写法被视为同一条记录；内容不是公网 IP
    （异常数据、历史遗留）时退化为原始字符串，仍会被判为「需要删除」。
    """
    return ips.canonicalize(record.content) or record.content


def compute_diff(existing_records, target_ips):
    """计算差量。

    Args:
        existing_records: 受管辖类型下的现有记录
        target_ips: 目标 IP 列表（规范化字符串）

    Returns:
        tuple[list[str], list[ManagedRecord]]: (待新增 IP 列表, 待删除记录列表)
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
    values = {record.proxied for record in existing_records if record.type == record_type}
    if len(values) == 1:
        return values.pop()
    if len(values) > 1:
        print(f"警告: 现有 {record_type} 记录的 proxied 取值不一致，新建记录使用 False")
    return False


def plan_sync(name, target_ips, store):
    """算出本次同步计划，不做任何写操作。

    Args:
        name: DNS 记录名
        target_ips: 目标 IP 列表（规范化字符串）
        store: 记录存储（只用到「列出记录名下的管辖记录」这一操作）

    Returns:
        SyncPlan: 差量计划

    Raises:
        DnsSyncError: 目标 IP 列表为空 —— 此时拒绝同步，绝不执行删除，
            避免上游解析异常把该记录名的解析清空
    """
    if not target_ips:
        raise DnsSyncError("目标 IP 列表为空，拒绝同步（不执行任何新增或删除）")

    existing_records = store.list_records(name)
    to_create, to_delete = compute_diff(existing_records, target_ips)

    proxied_by_type = {}
    for record_type in MANAGED_TYPES:
        if any(ips.record_type(ips.parse_ip(ip)) == record_type for ip in to_create):
            proxied_by_type[record_type] = _resolve_proxied(existing_records, record_type)

    print(f"待新增 {len(to_create)} 条，待删除 {len(to_delete)} 条")
    if not to_create and not to_delete:
        print(f"{name} 的 DNS 记录已与优选 IP 一致，无需更新")

    return SyncPlan(to_create=to_create, to_delete=to_delete, proxied_by_type=proxied_by_type)


def apply_sync(name, plan, store):
    """按计划执行同步：先新增缺失记录，再删除多余旧记录。

    先增后删保证同步过程中该记录名始终保有可用的解析记录（见
    ``docs/adr/0002-create-before-delete-sync-order.md``）；单条失败不中断其余条目，
    也不做事务回滚。

    消息在此一次性产出最终文案（失败消息自带「失败: 」前缀），推送内容只有一个来源，
    CLI 不再二次加工。

    Args:
        name: DNS 记录名
        plan: ``plan_sync`` 算出的计划
        store: 记录存储（用到「新建一条记录」与「删除一条记录」两个操作）

    Returns:
        list[str]: 最终消息列表（成功与失败按发生顺序混排，失败自带前缀）
    """
    messages = []

    for ip in plan.to_create:
        try:
            record_type, content = _canonical_create_target(name, ip)
            store.create_record(name, record_type, content, proxied=plan.proxied_by_type.get(record_type, False))
        except Exception as e:
            messages.append(f"失败: ip:{ip} 新增 {name} 失败 - {e}")
            continue
        messages.append(f"ip:{content} 解析 {name} 成功")

    for record in plan.to_delete:
        try:
            store.delete_record(record.id)
            messages.append(f"ip:{record.content} 删除旧记录成功")
        except Exception as e:
            messages.append(f"失败: ip:{record.content} 删除旧记录失败 - {e}")

    return messages
