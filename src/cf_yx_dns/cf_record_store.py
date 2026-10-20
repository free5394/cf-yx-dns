"""记录存储的 Cloudflare 实现：唯一接触 Cloudflare SDK 的一层。

分层（新增测试该写在哪一层）：

- **seam**：``cf_records`` 的两个同步动词（算计划、执行计划）只通过「记录存储」的三个业务
  操作以及 ``ManagedRecord`` 值类型说话 —— 列出记录名下的管辖记录、新建一条记录、删除一条
  记录。这一层不认识 SDK、Zone、Token、TTL，也不知道页对象是什么。
- **适配器**：本模块。它把 SDK 记录对象映射成 ``ManagedRecord``，把「按类型分别查询并自动
  翻页」留在内部，独占 TTL 与备注（comment）这类只属于 Cloudflare 的参数，并负责 Zone 与
  Token 的读取和客户端缓存。

要断言同步行为（差量、先增后删、单条失败不中断），在 seam 上注入替身；要断言 SDK 的属性
路径与参数形状，写进 ``tests/test_cf_records.py`` 里那条跨真实 seam 的测试（真实 SDK + 模拟
HTTP 传输）—— 它是仓库里唯一一条这样的测试，SDK 变化时也只有它会红。
"""

import time

from cloudflare import Cloudflare

from .cf_records import MANAGED_TYPES, ManagedRecord

# 新建记录的 TTL（秒）：只属于 Cloudflare 的参数，留在本实现里
DEFAULT_TTL = 300


class CloudflareRecordStore:
    """按 Cloudflare SDK 实现的记录存储。

    Zone 与 Token 在构造时读取一次，之后不再读环境变量；客户端按实例缓存 —— 同一个记录
    存储实例复用同一个客户端。

    Args:
        api_token: Cloudflare API Token
        zone_id: 记录名所属的 Zone
    """

    def __init__(self, api_token, zone_id):
        self._api_token = api_token
        self._zone_id = zone_id
        self._client = None

    def _client_instance(self):
        """按需创建并缓存客户端。"""
        if self._client is None:
            self._client = Cloudflare(api_token=self._api_token)
        return self._client

    def list_records(self, name):
        """列出该记录名下受管辖类型（A/AAAA）的记录。

        按类型分别查询并通过页对象迭代自动翻页：翻页由 SDK 负责，判据是「还有没有下一页」，
        不依赖未在 SDK 模型中声明的 ``result_info.total_count``。

        Args:
            name: 记录名

        Returns:
            list[ManagedRecord]: 每条含 id/name/type/content/proxied
        """
        records = []
        for record_type in MANAGED_TYPES:
            for record in self._client_instance().dns.records.list(
                zone_id=self._zone_id,
                name={"exact": name},
                type=record_type,
            ):
                records.append(
                    ManagedRecord(
                        id=record.id,
                        name=record.name,
                        type=record.type,
                        content=record.content,
                        proxied=bool(record.proxied),
                    )
                )
                print(f"现有记录: {record.type} {record.name} -> {record.content} (proxied: {record.proxied})")
        print(f"获取记录数: {len(records)}")
        return records

    def create_record(self, name, record_type, content, *, proxied=False):
        """新建一条记录。

        Args:
            name: 记录名
            record_type: 记录类型（A/AAAA）
            content: 记录内容（规范化 IP）
            proxied: 是否走 Cloudflare 代理；显式传入，不依赖 API 的隐式默认

        Returns:
            None: 成功即返回，失败抛异常

        Raises:
            cloudflare.APIError: Cloudflare 接口报错
        """
        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._client_instance().dns.records.create(
            zone_id=self._zone_id,
            name=name,
            content=content,
            ttl=DEFAULT_TTL,
            type=record_type,
            proxied=proxied,
            comment=current_time,
        )
        print(
            f"dns add success: ---- Time: {current_time} ---- "
            f"ip:{content} (type: {record_type}, proxied: {proxied})"
        )

    def delete_record(self, record_id):
        """按记录 ID 删除一条记录。

        Args:
            record_id: DNS 记录 ID

        Returns:
            None: 成功即返回，失败抛异常

        Raises:
            cloudflare.APIError: Cloudflare 接口报错
        """
        self._client_instance().dns.records.delete(dns_record_id=record_id, zone_id=self._zone_id)
