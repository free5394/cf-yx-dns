"""cf_records / cf_record_store 的测试。

测试分两层，对应这条 seam：

- ``cf_records`` 的测试只注入**记录存储替身**（``FakeRecordStore``）：替身只用业务语言实现
  三个操作（列出记录名下的管辖记录、新建一条、删除一条），不认识 SDK、Zone、Token，
  因此断言里不出现 ``zone_id`` 这类 SDK 参数名，也不复刻 SDK 的属性路径。
- ``cf_record_store`` 只有一条测试跨真实 seam：真实 Cloudflare SDK + 模拟 HTTP 传输。
  分页要覆盖 A/AAAA 两类，新建记录只断言参数形状（zone_id、name/type/content、ttl、comment）。
"""

import json
from types import SimpleNamespace

import httpx
import pytest
from cloudflare import Cloudflare

from cf_yx_dns import cf_record_store, cf_records
from cf_yx_dns.cf_records import DnsSyncError, ManagedRecord, SyncPlan

NAME = "dns.example.com"


# --- 记录存储替身 ---------------------------------------------------------


class FakeRecordStore:
    """记录存储替身（seam 的第二个实现）。

    Args:
        existing_records: 记录名下的现有记录
        create_error: 触发时 ``create_record`` 抛出的异常
        delete_error: 触发时 ``delete_record`` 抛出的异常
    """

    def __init__(self, existing_records=(), create_error=None, delete_error=None):
        self.existing_records = list(existing_records)
        self.create_error = create_error
        self.delete_error = delete_error
        self.events = []
        self.created = []
        self.deleted = []

    def list_records(self, name):
        self.events.append("list")
        return list(self.existing_records)

    def create_record(self, name, record_type, content, *, proxied=False):
        self.events.append("create")
        self.created.append({"name": name, "type": record_type, "content": content, "proxied": proxied})
        if self.create_error is not None:
            raise self.create_error

    def delete_record(self, record_id):
        self.events.append("delete")
        self.deleted.append(record_id)
        if self.delete_error is not None:
            raise self.delete_error


def make_record(record_id="rec-1", content="104.16.1.1", record_type="A", proxied=False):
    """构造一条受管辖的现有记录。"""
    return ManagedRecord(id=record_id, name=NAME, type=record_type, content=content, proxied=proxied)


# --- compute_diff ---------------------------------------------------------


class TestComputeDiff:
    def test_no_diff_when_identical(self):
        """现有记录与目标一致：无新增、无删除。"""
        existing = [make_record(content="104.16.1.1"), make_record("rec-2", "104.16.1.2")]
        to_create, to_delete = cf_records.compute_diff(existing, ["104.16.1.1", "104.16.1.2"])
        assert to_create == []
        assert to_delete == []

    def test_missing_ip_is_created(self):
        """目标里缺的 IP 判为待新增。"""
        existing = [make_record(content="104.16.1.1")]
        to_create, to_delete = cf_records.compute_diff(existing, ["104.16.1.1", "104.16.1.2"])
        assert to_create == ["104.16.1.2"]
        assert to_delete == []

    def test_stale_record_is_deleted(self):
        """目标里没有的旧记录判为待删除。"""
        stale = make_record(content="104.16.1.9")
        to_create, to_delete = cf_records.compute_diff([stale], ["104.16.1.1"])
        assert to_create == ["104.16.1.1"]
        assert to_delete == [stale]

    def test_dedupes_targets(self):
        """目标重复只留一份。"""
        to_create, to_delete = cf_records.compute_diff([], ["104.16.1.1", "104.16.1.1"])
        assert to_create == ["104.16.1.1"]
        assert to_delete == []

    def test_ipv6_equivalent_written_forms_are_the_same_record(self):
        """展开写法与压缩写法是同一个地址：既不当作新增，也不删除。"""
        expanded = make_record(record_type="AAAA", content="2606:4700:0000:0000:0000:0000:0000:0001")
        to_create, to_delete = cf_records.compute_diff([expanded], ["2606:4700::1"])
        assert to_create == []
        assert to_delete == []

    def test_content_that_is_not_a_public_ip_is_deleted_as_is(self):
        """内容不是公网 IP（历史遗留）时按原始字符串比较，不去猜里面的地址。"""
        legacy = make_record(content="104.16.1.1 # 遗留注释")
        to_create, to_delete = cf_records.compute_diff([legacy], ["104.16.1.1"])
        assert to_create == ["104.16.1.1"]  # 整条内容不匹配，这个 IP 仍算缺
        assert to_delete == [legacy]


# --- plan_sync ------------------------------------------------------------


class TestPlanSync:
    def test_empty_target_raises_before_any_record_operation(self):
        """空目标列表：在任何新增/删除之前就拒绝同步（空列表护栏）。"""
        store = FakeRecordStore([make_record()])
        with pytest.raises(DnsSyncError):
            cf_records.plan_sync(NAME, [], store)
        assert store.events == []  # 连读都没发生，更不会有删除

    def test_plan_inherits_proxied_from_existing_records_of_same_type(self):
        """现有同类记录 proxied 一致时沿用该值。"""
        store = FakeRecordStore([make_record(proxied=True)])
        plan = cf_records.plan_sync(NAME, ["104.16.1.1", "104.16.2.2"], store)
        assert plan.to_create == ["104.16.2.2"]
        assert plan.proxied_by_type == {"A": True}

    def test_plan_defaults_proxied_to_false_without_existing_records(self):
        """没有同类现有记录时用 False（Cloudflare 默认值）。"""
        plan = cf_records.plan_sync(NAME, ["104.16.1.1"], FakeRecordStore())
        assert plan.to_create == ["104.16.1.1"]
        assert plan.proxied_by_type == {"A": False}

    def test_plan_warns_and_defaults_to_false_when_proxied_values_disagree(self, capsys):
        """现有同类记录 proxied 取值不一致：不猜，退回 False 并告警。"""
        store = FakeRecordStore([make_record("rec-1", "104.16.1.1", proxied=True),
                                 make_record("rec-2", "104.16.1.2", proxied=False)])
        plan = cf_records.plan_sync(NAME, ["104.16.1.1", "104.16.1.2", "104.16.2.2"], store)
        assert plan.proxied_by_type == {"A": False}
        assert "proxied 取值不一致" in capsys.readouterr().out

    def test_plan_is_empty_when_records_already_match(self):
        """记录已与优选 IP 一致：计划为空。"""
        store = FakeRecordStore([make_record(content="104.16.1.1")])
        plan = cf_records.plan_sync(NAME, ["104.16.1.1"], store)
        assert plan.is_empty


# --- apply_sync -----------------------------------------------------------


class TestApplySync:
    def test_creates_before_deleting_and_carries_type_and_proxied(self):
        """先增后删，且新增时带上记录类型与沿用的 proxied 值。"""
        plan = SyncPlan(
            to_create=["104.16.2.2"],
            to_delete=[make_record(content="104.16.1.1")],
            proxied_by_type={"A": True},
        )
        store = FakeRecordStore([make_record(content="104.16.1.1")])

        messages = cf_records.apply_sync(NAME, plan, store)

        assert store.events == ["create", "delete"]
        assert store.created[0]["content"] == "104.16.2.2"
        assert store.created[0]["type"] == "A"
        assert store.created[0]["proxied"] is True
        assert messages == [f"ip:104.16.2.2 解析 {NAME} 成功", "ip:104.16.1.1 删除旧记录成功"]

    def test_ipv6_target_uses_aaaa_type(self):
        """IPv6 目标写入时记录类型为 AAAA。"""
        plan = SyncPlan(to_create=["2606:4700:0:9623::82a0"], proxied_by_type={"AAAA": False})
        store = FakeRecordStore()

        cf_records.apply_sync(NAME, plan, store)

        assert store.created[0]["type"] == "AAAA"
        assert store.created[0]["content"] == "2606:4700:0:9623::82a0"  # 写入前已规范化

    def test_create_failure_is_collected_without_stopping_other_items(self):
        """单条新增失败只记一条失败消息，其余条目照常执行。"""
        plan = SyncPlan(to_create=["104.16.2.2", "104.16.2.3"], proxied_by_type={"A": False})
        store = FakeRecordStore(create_error=RuntimeError("boom"))

        messages = cf_records.apply_sync(NAME, plan, store)

        assert len(store.created) == 2
        assert len(messages) == 2
        assert all(m.startswith("失败: ") for m in messages)
        assert "boom" in messages[0]

    def test_delete_failure_is_collected(self):
        """删除失败同样收集成失败消息，不影响前面已成功的条目。"""
        plan = SyncPlan(to_create=["104.16.2.2"], to_delete=[make_record()], proxied_by_type={"A": False})
        store = FakeRecordStore(delete_error=RuntimeError("nope"))

        messages = cf_records.apply_sync(NAME, plan, store)

        assert messages[0] == f"ip:104.16.2.2 解析 {NAME} 成功"
        assert messages[1].startswith("失败: ip:104.16.1.1 删除旧记录失败")
        assert "nope" in messages[1]

    def test_invalid_ip_is_rejected_before_any_create_call(self):
        """非法目标 IP 在同步层就被拒，记录存储不会被调用。"""
        plan = SyncPlan(to_create=["999.1.1.1"], proxied_by_type={"A": False})
        store = FakeRecordStore()

        messages = cf_records.apply_sync(NAME, plan, store)

        assert messages == [f"失败: ip:999.1.1.1 新增 {NAME} 失败 - 记录名或 IP 非法: '999.1.1.1'"]
        assert store.created == []


# --- Cloudflare 记录存储实现 ----------------------------------------------


class FakeRecordsApi:
    """Cloudflare 客户端里 ``dns.records`` 的最小替身，只记录调用参数。"""

    def __init__(self):
        self.delete_calls = []

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class TestCloudflareRecordStore:
    def test_client_is_created_once_from_constructor_config(self, monkeypatch):
        """客户端随存储构造一次并复用；构造后改环境变量不影响已读到的配置。

        这是适配器自身的测试，因此会看到 ``zone_id`` 这类 SDK 参数名：zone 只能从
        「传给 SDK 的参数」这一处观测，否则无法证明配置是在构造期读到的。
        """
        clients = []
        api = FakeRecordsApi()

        def fake_cloudflare(api_token):
            clients.append(SimpleNamespace(api_token=api_token, dns=SimpleNamespace(records=api)))
            return clients[-1]

        monkeypatch.setattr(cf_record_store, "Cloudflare", fake_cloudflare)
        store = cf_record_store.CloudflareRecordStore(api_token="token", zone_id="zone-id")
        monkeypatch.setenv("CF_ZONE_ID", "调换后的-zone")

        store.delete_record("rec-1")
        store.delete_record("rec-2")

        assert len(clients) == 1  # 一个存储只构造一个客户端
        assert clients[0].api_token == "token"
        assert [call["zone_id"] for call in api.delete_calls] == ["zone-id", "zone-id"]

    def test_real_seam_pages_both_types_and_shapes_create(self, monkeypatch):
        """跨真实 seam 的唯一一条测试：真实 SDK + 模拟 HTTP 传输。

        覆盖三件只有真实实现才会出错的事：分页遍历（A 25 条跨 3 页、AAAA 3 条跨 2 页）、
        管辖范围的请求参数（name 走精确匹配，退化成模糊匹配就会把整个 zone 算进管辖范围），
        以及新建记录的参数形状（zone_id 进路径，name/type/content/ttl/proxied/comment 进请求体）。
        """
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json=_envelope({"id": "new-record-id"}))
            params = dict(request.url.params)
            pages = _A_PAGES if params["type"] == "A" else _AAAA_PAGES
            page = int(params.get("page", "1"))
            return httpx.Response(200, json=_envelope(pages[page - 1], _result_info(page)))

        monkeypatch.setattr(
            cf_record_store,
            "Cloudflare",
            lambda api_token: Cloudflare(
                api_token=api_token,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
        )
        store = cf_record_store.CloudflareRecordStore(api_token="token", zone_id="zone-id")

        records = store.list_records(NAME)

        assert [(dict(r.url.params)["type"], dict(r.url.params).get("page", "1")) for r in requests] == [
            ("A", "1"),
            ("A", "2"),
            ("A", "3"),
            ("AAAA", "1"),
            ("AAAA", "2"),
        ]
        # 管辖范围同样靠请求参数钉住：name 少传或退化成模糊匹配，管辖范围就会变成整个 zone
        assert [dict(r.url.params)["name.exact"] for r in requests] == [NAME] * 5
        assert len(records) == 28
        assert [record.type for record in records].count("A") == 25
        assert [record.type for record in records].count("AAAA") == 3
        assert records[0] == ManagedRecord("a-1", NAME, "A", "104.16.0.1", False)
        assert records[-1] == ManagedRecord("aaaa-3", NAME, "AAAA", "2606:4700::93", True)

        store.create_record(NAME, "AAAA", "2606:4700::9a", proxied=True)

        post = requests[-1]
        assert post.method == "POST"
        assert post.url.path.endswith("/zones/zone-id/dns_records")
        body = json.loads(post.content)
        assert body["name"] == NAME
        assert body["type"] == "AAAA"
        assert body["content"] == "2606:4700::9a"
        assert body["ttl"] == 300
        assert body["proxied"] is True
        assert body["comment"].strip()  # 新建记录带时间戳注释


def _payload(record_id, content, record_type, proxied):
    return {"id": record_id, "name": NAME, "type": record_type, "content": content, "proxied": proxied}


def _result_info(page):
    return {"page": page, "per_page": 20, "count": 1, "total_count": 1, "total_pages": 1}


def _envelope(result, result_info=None):
    body = {"success": True, "errors": [], "messages": [], "result": result}
    if result_info is not None:
        body["result_info"] = result_info
    return body


_A_PAGES = [
    [_payload(f"a-{i}", f"104.16.0.{i}", "A", False) for i in range(1, 21)],
    [_payload(f"a-{i}", f"104.17.0.{i}", "A", False) for i in range(1, 6)],
    [],
]

_AAAA_PAGES = [
    [_payload(f"aaaa-{i}", f"2606:4700::9{i}", "AAAA", True) for i in range(1, 4)],
    [],
]
