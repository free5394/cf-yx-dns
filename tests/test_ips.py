"""优选 IP 抓取、候选提取、校验与规范化的测试。"""

import ipaddress

import httpx
import pytest

from cf_yx_dns import config, ips


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


HTML_PAGE = """
<html><body>
<div class="ip">104.16.1.1#CF-IPDB_1</div>
<div class="ip">[2606:4700::1]#CF-IPv6_IPDB_1</div>
<div class="ip">[2606:4700::2]#CF-IPv6_IPDB_2</div>
</body></html>
"""


class TestFetchIpSource:
    def test_success_first_attempt(self, monkeypatch):
        calls = []

        def fake_get(url, timeout):
            calls.append((url, timeout))
            return FakeResponse("<html>104.16.1.1</html>")

        monkeypatch.setattr(ips.httpx, "get", fake_get)
        assert ips.fetch_ip_source() == "<html>104.16.1.1</html>"
        assert calls == [(config.DEFAULT_CF_YX_URL, config.DEFAULT_TIMEOUT)]

    def test_custom_url_and_timeout(self, monkeypatch):
        monkeypatch.setenv("CF_YX_URL", "https://example.com/list")
        seen = {}

        def fake_get(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return FakeResponse("104.16.1.1")

        monkeypatch.setattr(ips.httpx, "get", fake_get)
        assert ips.fetch_ip_source(timeout=3) == "104.16.1.1"
        assert seen == {"url": "https://example.com/list", "timeout": 3}

    def test_retries_with_exponential_backoff(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(ips.httpx, "get", lambda url, timeout: FakeResponse(status_code=500))
        monkeypatch.setattr(ips.time, "sleep", sleeps.append)
        assert ips.fetch_ip_source() is None
        assert sleeps == [1, 2, 4, 8]

    def test_succeeds_after_retry(self, monkeypatch):
        responses = [FakeResponse(status_code=503), FakeResponse("<html>ok</html>")]
        monkeypatch.setattr(ips.httpx, "get", lambda url, timeout: responses.pop(0))
        monkeypatch.setattr(ips.time, "sleep", lambda seconds: None)
        assert ips.fetch_ip_source() == "<html>ok</html>"

    def test_returns_none_when_all_attempts_raise(self, monkeypatch):
        def boom(url, timeout):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(ips.httpx, "get", boom)
        monkeypatch.setattr(ips.time, "sleep", lambda seconds: None)
        assert ips.fetch_ip_source(max_retries=3) is None


class TestCanonicalize:
    def test_ipv4_passthrough(self):
        assert ips.canonicalize("104.16.1.1") == "104.16.1.1"

    def test_ipv6_expanded_to_compressed(self):
        assert ips.canonicalize("2606:4700:0:9623:0:0:0:82a0") == "2606:4700:0:9623::82a0"

    def test_same_address_two_forms_are_equal(self):
        assert ips.canonicalize("2606:4700:0:9623:0:0:0:82a0") == ips.canonicalize("2606:4700:0:9623::82a0")

    @pytest.mark.parametrize(
        "text",
        ["10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.1.1", "224.0.0.1", "fc00::1", "::1"],
    )
    def test_rejects_non_public_addresses(self, text):
        assert ips.canonicalize(text) is None

    @pytest.mark.parametrize("text", ["", None, "1.2.3", "999.1.1.1", "not-an-ip", "[2606:4700::1]"])
    def test_rejects_invalid_input(self, text):
        assert ips.canonicalize(text) is None


class TestRecordType:
    def test_ipv4(self):
        assert ips.record_type(ipaddress.ip_address("104.16.1.1")) == "A"

    def test_ipv6(self):
        assert ips.record_type(ipaddress.ip_address("2606:4700::1")) == "AAAA"

    def test_non_address(self):
        assert ips.record_type("104.16.1.1") is None


class TestExtractCandidates:
    def test_html_candidates_in_order_and_deduped(self):
        text = "<p>104.16.1.1</p><p>104.16.1.1</p><p>[2606:4700::1]</p>"
        assert ips.extract_candidates(text) == ["104.16.1.1", "2606:4700::1"]

    def test_comment_suffix_is_ignored(self):
        assert ips.extract_candidates("[2606:4700::1]#CF-IPv6_IPDB_1") == ["2606:4700::1"]

    def test_plain_text_list_detects_unbracketed_ipv6(self):
        text = "104.16.1.1,2606:4700::1 , 104.16.2.1"
        assert ips.extract_candidates(text) == ["104.16.1.1", "104.16.2.1", "2606:4700::1"]

    def test_newline_separated_list(self):
        assert ips.extract_candidates("104.16.1.1\n104.16.2.1") == ["104.16.1.1", "104.16.2.1"]


class TestParseIpAddresses:
    def test_returns_canonical_list(self):
        assert ips.parse_ip_addresses(HTML_PAGE) == ["104.16.1.1", "2606:4700::1", "2606:4700::2"]

    def test_dedups_two_writings_of_same_ipv6(self):
        text = "<p>[2606:4700:0:9623:0:0:0:82a0]</p><p>[2606:4700:0:9623::82a0]</p>"
        assert ips.parse_ip_addresses(text) == ["2606:4700:0:9623::82a0"]

    def test_skips_non_public_candidates(self, capsys):
        text = "<p>10.0.0.1</p><p>104.16.1.1</p><p>127.0.0.1</p>"
        assert ips.parse_ip_addresses(text) == ["104.16.1.1"]
        assert "已跳过 2 个无效或非公网候选" in capsys.readouterr().out

    def test_none_when_no_candidate(self, capsys):
        assert ips.parse_ip_addresses("<html>no ip here</html>") is None
        assert "错误: 未解析到有效 IP 地址" in capsys.readouterr().out

    def test_none_when_all_candidates_filtered(self, capsys):
        assert ips.parse_ip_addresses("<p>192.168.1.1</p>") is None
        assert "错误: 未解析到有效 IP 地址" in capsys.readouterr().out

    def test_none_for_empty_input(self, capsys):
        assert ips.parse_ip_addresses("") is None
        assert "错误: 缺少必要的参数 (page_text)" in capsys.readouterr().out
