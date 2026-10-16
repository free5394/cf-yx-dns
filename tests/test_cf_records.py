"""Cloudflare DNS 记录差量同步的测试。

Cloudflare 客户端全程用替身（fake）：单测不打真实 API。其中翻页测试用真实 SDK +
httpx.MockTransport，用来锁住「迭代页对象会自动翻页、且以上游返回空页作为终止判据」
这一被依赖的行为。
"""

from types import SimpleNamespace

import httpx
import pytest
from cloudflare import Cloudflare

from cf_yx_dns import cf_records

NAME = "dns.example.com"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "token")
    monkeypatch.setenv("CF_ZONE_ID", "zone-id")
    monkeypatch.setenv("CF_DNS_NAME", NAME)
    monkeypatch.setattr(cf_records, "_client", None)
    monkeypatch.setattr(cf_records, "_client_token", None)


class FakeRecordsAPI:
    """只记录调用、返回预设数据的记录接口替身。"""

    def __init__(self, pages=None, create_error=None, delete_error=None):
        self.pages = pages or {}
        self.create_error = create_error
        self.delete_error = delete_error
        self.events = []
        self.list_calls = []
        self.create_calls = []
        self.delete_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        self.events.append(("list", kwargs))
        return [SimpleNamespace(**record) for record in self.pages.get(kwargs["type"], [])]

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        self.events.append(("create", kwargs))
        if self.create_error:
            raise self.create_error
        return SimpleNamespace(**kwargs)

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        self.events.append(("delete", kwargs))
        if self.delete_error:
            raise self.delete_error
        return SimpleNamespace(id=kwargs["dns_record_id"])


def install_fake_client(monkeypatch, records_api):
    monkeypatch.setattr(cf_records, "_client", SimpleNamespace(dns=SimpleNamespace(records=records_api)))
    monkeypatch.setattr(cf_records, "_client_token", "token")


def make_record(record_id, record_type, content, proxied=False):
    return {
        "id": record_id,
        "name": NAME,
        "type": record_type,
        "content": content,
        "proxied": proxied,
    }


class TestListDnsRecords:
    def test_queries_only_managed_types_with_exact_name(self, monkeypatch):
        api = FakeRecordsAPI(pages={"A": [make_record("rec-1", "A", "104.16.1.1")]})
        install_fake_client(monkeypatch, api)

        records = cf_records.list_dns_records(NAME)

        assert records == [
            {"id": "rec-1", "name": NAME, "type": "A", "content": "104.16.1.1", "proxied": False}
        ]
        assert [call["type"] for call in api.list_calls] == ["A", "AAAA"]
        assert all(call["name"] == {"exact": NAME} for call in api.list_calls)
        assert all(call["zone_id"] == "zone-id" for call in api.list_calls)

    def test_follows_sdk_auto_pagination(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            params = dict(request.url.params)
            if params.get("type") == "AAAA":
                return _page_response([], page=1)
            page = int(params.get("page", "1"))
            if page == 1:
                result = [_record_payload(index) for index in range(1, 21)]
            elif page == 2:
                result = [_record_payload(index) for index in range(21, 26)]
            else:
                result = []
            return _page_response(result, page=page)

        client = Cloudflare(api_token="token", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(cf_records, "_client", client)
        monkeypatch.setattr(cf_records, "_client_token", "token")

        records = cf_records.list_dns_records(NAME)

        assert len(records) == 25
        requested_pages = [dict(request.url.params).get("page", "1") for request in requests]
        assert requested_pages == ["1", "2", "3", "1"]


def _record_payload(index):
    return {
        "id": f"rec-{index}",
        "name": NAME,
        "type": "A",
        "content": f"104.16.{index}.1",
        "proxied": False,
    }


def _page_response(result, page):
    return httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "messages": [],
            "result": result,
            "result_info": {"page": page, "per_page": 100},
        },
    )


class TestComputeDiff:
    def test_creates_missing_and_deletes_extra(self):
        existing = [make_record("rec-1", "A", "104.16.1.1"), make_record("rec-2", "A", "104.16.9.9")]

        to_create, to_delete = cf_records.compute_diff(existing, ["104.16.1.1", "104.16.2.2"])

        assert to_create == ["104.16.2.2"]
        assert to_delete == [existing[1]]

    def test_no_churn_between_two_writings_of_same_ipv6(self):
        existing = [make_record("rec-1", "AAAA", "2606:4700:0:9623:0:0:0:82a0")]

        to_create, to_delete = cf_records.compute_diff(existing, ["2606:4700:0:9623::82a0"])

        assert to_create == []
        assert to_delete == []

    def test_duplicate_target_ip_created_once(self):
        to_create, to_delete = cf_records.compute_diff([], ["104.16.1.1", "104.16.1.1"])

        assert to_create == ["104.16.1.1"]
        assert to_delete == []

    def test_non_ip_content_is_deleted(self):
        existing = [make_record("rec-1", "A", "legacy-value")]

        to_create, to_delete = cf_records.compute_diff(existing, ["104.16.1.1"])

        assert to_create == ["104.16.1.1"]
        assert to_delete == existing


class TestPlanSync:
    def test_empty_target_raises_before_any_api_call(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)

        with pytest.raises(cf_records.DnsSyncError):
            cf_records.plan_sync(NAME, [])

        assert api.list_calls == []

    def test_proxied_inherited_from_existing_records(self, monkeypatch):
        api = FakeRecordsAPI(pages={"A": [make_record("rec-1", "A", "104.16.1.1", proxied=True)]})
        install_fake_client(monkeypatch, api)

        plan = cf_records.plan_sync(NAME, ["104.16.1.1", "104.16.2.2"])

        assert plan.to_create == ["104.16.2.2"]
        assert plan.proxied_by_type == {"A": True}

    def test_proxied_defaults_false_without_existing_records(self, monkeypatch):
        install_fake_client(monkeypatch, FakeRecordsAPI())

        plan = cf_records.plan_sync(NAME, ["104.16.1.1"])

        assert plan.proxied_by_type == {"A": False}

    def test_mixed_proxied_falls_back_to_false(self, monkeypatch, capsys):
        api = FakeRecordsAPI(
            pages={
                "A": [
                    make_record("rec-1", "A", "104.16.1.1", proxied=True),
                    make_record("rec-2", "A", "104.16.2.2", proxied=False),
                ]
            }
        )
        install_fake_client(monkeypatch, api)

        plan = cf_records.plan_sync(NAME, ["104.16.1.1", "104.16.2.2", "104.16.3.3"])

        assert plan.proxied_by_type == {"A": False}
        assert "proxied 取值不一致" in capsys.readouterr().out

    def test_plan_is_empty_when_records_already_match(self, monkeypatch, capsys):
        api = FakeRecordsAPI(pages={"A": [make_record("rec-1", "A", "104.16.1.1")]})
        install_fake_client(monkeypatch, api)

        plan = cf_records.plan_sync(NAME, ["104.16.1.1"])

        assert plan.is_empty is True
        assert "无需更新" in capsys.readouterr().out


class TestApplySync:
    def test_creates_before_deleting_and_carries_proxied(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)
        plan = cf_records.SyncPlan(
            to_create=["104.16.2.2"],
            to_delete=[make_record("rec-1", "A", "104.16.1.1")],
            proxied_by_type={"A": True},
        )

        success, errors = cf_records.apply_sync(NAME, plan)

        assert errors == []
        assert [event for event, _ in api.events] == ["create", "delete"]
        assert api.create_calls[0]["content"] == "104.16.2.2"
        assert api.create_calls[0]["proxied"] is True
        assert api.delete_calls[0] == {"dns_record_id": "rec-1", "zone_id": "zone-id"}
        assert len(success) == 2

    def test_single_create_failure_does_not_stop_others(self, monkeypatch):
        api = FakeRecordsAPI(create_error=RuntimeError("api down"))
        install_fake_client(monkeypatch, api)
        plan = cf_records.SyncPlan(to_create=["104.16.1.1", "104.16.2.2"], proxied_by_type={"A": False})

        success, errors = cf_records.apply_sync(NAME, plan)

        assert success == []
        assert len(errors) == 2
        assert all("新增" in message for message in errors)
        assert len(api.create_calls) == 2

    def test_delete_failure_is_collected_not_swallowed(self, monkeypatch):
        api = FakeRecordsAPI(delete_error=RuntimeError("api down"))
        install_fake_client(monkeypatch, api)
        plan = cf_records.SyncPlan(to_delete=[make_record("rec-1", "A", "104.16.1.1")])

        success, errors = cf_records.apply_sync(NAME, plan)

        assert success == []
        assert len(errors) == 1
        assert "删除旧记录失败" in errors[0]


class TestRecordCrud:
    def test_create_passes_canonical_content_and_explicit_proxied(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)

        message = cf_records.create_dns_record(NAME, "2606:4700:0:9623:0:0:0:82a0")

        call = api.create_calls[0]
        assert call["content"] == "2606:4700:0:9623::82a0"
        assert call["type"] == "AAAA"
        assert call["proxied"] is False
        assert call["ttl"] == cf_records.DEFAULT_TTL
        assert call["zone_id"] == "zone-id"
        assert "2606:4700:0:9623::82a0" in message

    def test_create_rejects_invalid_ip(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)

        with pytest.raises(cf_records.DnsSyncError):
            cf_records.create_dns_record(NAME, "999.1.1.1")

        assert api.create_calls == []

    def test_create_propagates_api_error(self, monkeypatch):
        install_fake_client(monkeypatch, FakeRecordsAPI(create_error=RuntimeError("api down")))

        with pytest.raises(RuntimeError):
            cf_records.create_dns_record(NAME, "104.16.1.1")

    def test_delete_requires_record_id(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)

        with pytest.raises(cf_records.DnsSyncError):
            cf_records.delete_dns_record(None)

        assert api.delete_calls == []

    def test_delete_returns_none_and_passes_ids(self, monkeypatch):
        api = FakeRecordsAPI()
        install_fake_client(monkeypatch, api)

        assert cf_records.delete_dns_record("rec-1") is None
        assert api.delete_calls == [{"dns_record_id": "rec-1", "zone_id": "zone-id"}]


class TestGetClient:
    def test_client_cached_per_token(self, monkeypatch):
        monkeypatch.setenv("CF_API_TOKEN", "token-1")
        created = []

        def fake_cloudflare(api_token):
            created.append(api_token)
            return SimpleNamespace(api_token=api_token)

        monkeypatch.setattr(cf_records, "Cloudflare", fake_cloudflare)

        first = cf_records.get_client()
        second = cf_records.get_client()

        assert first is second
        assert created == ["token-1"]

    def test_client_rebuilt_when_token_changes(self, monkeypatch):
        created = []

        def fake_cloudflare(api_token):
            created.append(api_token)
            return SimpleNamespace(api_token=api_token)

        monkeypatch.setattr(cf_records, "Cloudflare", fake_cloudflare)
        monkeypatch.setenv("CF_API_TOKEN", "token-1")
        first = cf_records.get_client()
        monkeypatch.setenv("CF_API_TOKEN", "token-2")
        second = cf_records.get_client()

        assert first is not second
        assert created == ["token-1", "token-2"]
