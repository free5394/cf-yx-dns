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

from cloudflare import APIStatusError, Cloudflare

from .cf_records import MANAGED_TYPES, ManagedRecord, RecordAlreadyExists

# 新建记录的 TTL（秒）：只属于 Cloudflare 的参数，留在本实现里
DEFAULT_TTL = 300

# 「内容完全相同的记录已存在」的错误码：81058。生产实测（issue #6，专用测试名
# f1-probe.seismic.ccwu.cc）里 type+name+content+proxied 全同的重复创建返回
# HTTP 400 / ``errors[0].code = 81058`` / "An identical record already exists."。
#
# 81057 "Record already exists." 的语义**尚未实测**（certbot 把它与 81058 并列按成功兜底，
# 但那是猜的）：不纳入判据，抛出去才是对的。真要用，先补一次实测。
DUPLICATE_RECORD_CODES = frozenset({81058})


def _duplicate_record_code(error):
    """取出「记录已存在」类的错误码；不是这类错误返回 None。

    错误码只存在于响应体里：SDK 5.1.0 的 ``str(error)`` 只有 message 文本，异常对象也没有
    ``.code`` 属性，所以判据必须读 ``error.body["errors"][*]["code"]``，不能匹配文案（文案随
    API 版本变，实测文案与 certbot 注释里的措辞就不一致）。

    遍历全部 ``errors`` 而不是只看第 0 条：一次响应理论上可以带多条错误，任一条命中判据即可。

    Args:
        error: 记录存储的写操作抛出的异常

    Returns:
        int | None: 81058，或 None（不是这类错误）
    """
    if not isinstance(error, APIStatusError):
        return None
    body = error.body
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if not isinstance(errors, list):
        return None
    for item in errors:
        if isinstance(item, dict) and item.get("code") in DUPLICATE_RECORD_CODES:
            return item["code"]
    return None


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
            RecordAlreadyExists: Cloudflare 报告「内容完全相同的记录已存在」（81058）
            cloudflare.APIError: 其余 Cloudflare 接口报错
        """
        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        try:
            self._client_instance().dns.records.create(
                zone_id=self._zone_id,
                name=name,
                content=content,
                ttl=DEFAULT_TTL,
                type=record_type,
                proxied=proxied,
                comment=current_time,
            )
        except APIStatusError as error:
            code = _duplicate_record_code(error)
            if code is None:
                raise
            # 翻成领域异常：判据（错误码）留在本层，怎么处理由同步层决定
            raise RecordAlreadyExists(str(error), code) from error
        print(
            f"新增记录成功: ---- 时间: {current_time} ---- "
            f"ip:{content}（类型: {record_type}，代理: {proxied}）"
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
