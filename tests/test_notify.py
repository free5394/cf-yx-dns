"""PushPlus 推送的测试。"""

import pytest

from cf_yx_dns import config, notify


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "pp-token")
    monkeypatch.setenv("CF_DNS_NAME", "dns.example.com")
    monkeypatch.setenv("CF_ZONE_ID", "zone-id")


class TestPushPlus:
    def test_success_payload_and_returns_true(self, monkeypatch):
        calls = []

        def fake_post(url, json, timeout):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse(payload={"code": 200, "msg": "请求成功"})

        monkeypatch.setattr(notify.httpx, "post", fake_post)

        assert notify.push_plus("正文") is True
        assert calls[0]["url"] == notify.PUSHPLUS_URL
        assert calls[0]["timeout"] == config.DEFAULT_TIMEOUT
        assert calls[0]["json"] == {
            "token": "pp-token",
            "title": notify.SUCCESS_TITLE,
            "content": "正文",
            "template": "markdown",
            "channel": "wechat",
        }

    def test_string_code_200_is_success(self, monkeypatch):
        monkeypatch.setattr(
            notify.httpx, "post", lambda url, json, timeout: FakeResponse(payload={"code": "200"})
        )

        assert notify.push_plus("正文") is True

    def test_business_error_code_returns_false(self, monkeypatch, capsys):
        monkeypatch.setattr(
            notify.httpx,
            "post",
            lambda url, json, timeout: FakeResponse(payload={"code": 500, "msg": "token 无效"}),
        )

        assert notify.push_plus("正文") is False
        assert "消息推送失败" in capsys.readouterr().out

    def test_http_error_returns_false(self, monkeypatch):
        monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: FakeResponse(status_code=502))

        assert notify.push_plus("正文") is False

    def test_invalid_json_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            notify.httpx,
            "post",
            lambda url, json, timeout: FakeResponse(json_error=ValueError("not json")),
        )

        assert notify.push_plus("正文") is False

    def test_network_error_returns_false(self, monkeypatch):
        def boom(url, json, timeout):
            raise OSError("network down")

        monkeypatch.setattr(notify.httpx, "post", boom)

        assert notify.push_plus("正文") is False

    def test_without_token_skips(self, monkeypatch, capsys):
        monkeypatch.delenv("PUSHPLUS_TOKEN")
        monkeypatch.setattr(notify.httpx, "post", lambda **kwargs: pytest.fail("未配置 Token 不应发起请求"))

        assert notify.push_plus("正文") is None
        assert "跳过消息推送" in capsys.readouterr().out

    def test_title_override_reads_token_from_config(self, monkeypatch):
        seen = {}

        def fake_post(url, json, timeout):
            seen.update(json)
            return FakeResponse(payload={"code": 200})

        monkeypatch.setattr(notify.httpx, "post", fake_post)
        monkeypatch.delenv("PUSHPLUS_TOKEN")
        monkeypatch.setattr(config, "pushplus_token", lambda: "from-config")

        assert notify.push_plus("正文", title="自定义标题") is True
        assert seen["token"] == "from-config"
        assert seen["title"] == "自定义标题"


class TestFailureContent:
    def test_includes_name_zone_and_reason(self):
        content = notify.failure_content("抓取优选 IP 失败")

        assert "抓取优选 IP 失败" in content
        assert "dns.example.com" in content
        assert "zone-id" in content

    def test_marks_missing_configuration(self, monkeypatch):
        monkeypatch.delenv("CF_DNS_NAME")
        monkeypatch.delenv("CF_ZONE_ID")

        content = notify.failure_content("boom")

        assert "(未设置 CF_DNS_NAME)" in content
        assert "(未设置 CF_ZONE_ID)" in content
