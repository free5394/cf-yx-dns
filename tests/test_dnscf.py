# -*- coding: utf-8 -*-
"""dnscf 核心逻辑单元测试

覆盖：IP 验证与解析工具函数、差量同步策略、main 退出码
"""
from unittest import mock

import dnscf

# main() 正常运行所需的最小环境变量
ENV_OK = {"CF_API_TOKEN": "x", "CF_ZONE_ID": "y", "CF_DNS_NAME": "z"}

# 模拟 get_dns_records 返回的现有记录
EXISTING_RECORDS = [
    {"id": "r1", "name": "t", "type": "A", "content": "1.1.1.1", "comment": None},
    {"id": "r2", "name": "t", "type": "A", "content": "2.2.2.2", "comment": None},
    {"id": "r3", "name": "t", "type": "A", "content": "3.3.3.3", "comment": None},
]


# ---------- IP 验证工具 ----------

class TestIpValidation:
    def test_valid_ipv4(self):
        assert dnscf._is_valid_ip("104.16.1.1") is True

    def test_invalid_ipv4(self):
        assert dnscf._is_valid_ip("999.1.1.1") is False

    def test_valid_ipv6(self):
        assert dnscf._is_valid_ip("2606:4700::1") is True

    def test_ipv4_type(self):
        assert dnscf._get_ip_type("1.2.3.4") == "A"

    def test_ipv6_type(self):
        assert dnscf._get_ip_type("2606:4700::1") == "AAAA"

    def test_invalid_type(self):
        assert dnscf._get_ip_type("not-an-ip") is None


# ---------- IP 清理与解析 ----------

class TestCleanIpAddress:
    def test_bracketed_ipv6_with_comment(self):
        assert dnscf._clean_ip_address("[2606:4700::1]#CF-IPv6_IPDB_1") == "2606:4700::1"

    def test_plain_with_whitespace(self):
        assert dnscf._clean_ip_address("  1.2.3.4  ") == "1.2.3.4"

    def test_empty_and_none(self):
        assert dnscf._clean_ip_address("") is None
        assert dnscf._clean_ip_address(None) is None


class TestParseIpAddresses:
    def test_plain_comma_list(self):
        assert dnscf.parse_ip_addresses("1.2.3.4,5.6.7.8") == ["1.2.3.4", "5.6.7.8"]

    def test_newline_separated(self):
        assert dnscf.parse_ip_addresses("1.2.3.4\n5.6.7.8") == ["1.2.3.4", "5.6.7.8"]

    def test_html_with_ipv6_comment(self):
        html = "<td>[104.16.1.1]</td><td>[2606:4700::2df7:23e0:8908:4752]#CF-IPv6_IPDB_1</td>"
        ips = dnscf.parse_ip_addresses(html)
        assert "104.16.1.1" in ips
        assert "2606:4700::2df7:23e0:8908:4752" in ips

    def test_invalid_ips_filtered(self):
        assert dnscf.parse_ip_addresses("999.1.1.1, 1.2.3.4") == ["1.2.3.4"]

    def test_dedup_keeps_order(self):
        assert dnscf.parse_ip_addresses("1.2.3.4,5.6.7.8,1.2.3.4") == ["1.2.3.4", "5.6.7.8"]

    def test_empty_input_returns_none(self):
        assert dnscf.parse_ip_addresses("") is None

    def test_no_valid_ip_returns_none(self):
        assert dnscf.parse_ip_addresses("hello world") is None


# ---------- 差量同步 ----------

class TestSyncDnsRecords:
    def test_diff_create_then_delete(self):
        calls = []

        def fake_add(name, ip):
            calls.append(("add", ip))
            return f"ip:{ip} 解析 {name} 成功"

        def fake_del(record_id):
            calls.append(("del", record_id))

        with mock.patch.object(dnscf, "get_dns_records", return_value=EXISTING_RECORDS), \
             mock.patch.object(dnscf, "add_dns_record", fake_add), \
             mock.patch.object(dnscf, "del_dns_record", fake_del):
            success, errors = dnscf.sync_dns_records("t", ["2.2.2.2", "4.4.4.4"])

        # 先新增后删除
        assert [c[0] for c in calls] == ["add", "del", "del"]
        assert calls[0][1] == "4.4.4.4"
        assert [c[1] for c in calls[1:]] == ["r1", "r3"]
        assert errors == []

    def test_single_failure_does_not_abort(self):
        def fake_add(name, ip):
            if ip == "4.4.4.4":
                raise RuntimeError("模拟限流")
            return f"ip:{ip} 解析 {name} 成功"

        with mock.patch.object(dnscf, "get_dns_records", return_value=EXISTING_RECORDS), \
             mock.patch.object(dnscf, "add_dns_record", fake_add), \
             mock.patch.object(dnscf, "del_dns_record", lambda rid: None):
            success, errors = dnscf.sync_dns_records("t", ["2.2.2.2", "4.4.4.4"])

        # 新增 4.4.4.4 失败被收集，删除 r1/r3 仍全部完成
        assert len(errors) == 1
        assert "4.4.4.4" in errors[0]
        assert len(success) == 2

    def test_noop_when_identical(self):
        with mock.patch.object(dnscf, "get_dns_records", return_value=EXISTING_RECORDS), \
             mock.patch.object(dnscf, "add_dns_record") as add_mock, \
             mock.patch.object(dnscf, "del_dns_record") as del_mock:
            success, errors = dnscf.sync_dns_records("t", ["1.1.1.1", "2.2.2.2", "3.3.3.3"])

        add_mock.assert_not_called()
        del_mock.assert_not_called()
        assert success == [] and errors == []

    def test_noop_prints_fresh_hint(self, capsys):
        """无差量时应输出明确的"无需更新"提示，避免误判为未执行"""
        with mock.patch.object(dnscf, "get_dns_records", return_value=EXISTING_RECORDS):
            dnscf.sync_dns_records("t", ["1.1.1.1", "2.2.2.2", "3.3.3.3"])

        out = capsys.readouterr().out
        assert "待新增 0 条，待删除 0 条" in out
        assert "无需更新" in out

    def test_get_records_failure_reported(self):
        with mock.patch.object(dnscf, "get_dns_records", side_effect=RuntimeError("认证失败")):
            success, errors = dnscf.sync_dns_records("t", ["1.2.3.4"])

        assert success == []
        assert len(errors) == 1
        assert "认证失败" in errors[0]


# ---------- 差量计算 ----------

class TestComputeDiff:
    def test_diff_sets(self):
        to_create, to_delete = dnscf._compute_diff(EXISTING_RECORDS, ["2.2.2.2", "4.4.4.4"])
        assert to_create == ["4.4.4.4"]
        assert [r["id"] for r in to_delete] == ["r1", "r3"]

    def test_identical_means_empty_diff(self):
        to_create, to_delete = dnscf._compute_diff(EXISTING_RECORDS, ["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        assert to_create == [] and to_delete == []


# ---------- 优选 IP 获取与重试 ----------

class TestGetCfSpeedTestIp:
    def test_success_first_try(self):
        resp = mock.Mock(status_code=200, text="1.2.3.4")
        with mock.patch("dnscf.httpx.get", return_value=resp), \
             mock.patch("dnscf.time.sleep") as sleep_mock:
            assert dnscf.get_cf_speed_test_ip() == "1.2.3.4"
        sleep_mock.assert_not_called()

    def test_retries_on_non_200_then_none(self):
        resp = mock.Mock(status_code=503, text="")
        with mock.patch("dnscf.httpx.get", return_value=resp), \
             mock.patch("dnscf.time.sleep") as sleep_mock:
            assert dnscf.get_cf_speed_test_ip(max_retries=3) is None
        # 最后一次失败后不再等待
        assert sleep_mock.call_count == 2

    def test_exponential_backoff(self):
        resp = mock.Mock(status_code=500, text="")
        with mock.patch("dnscf.httpx.get", return_value=resp), \
             mock.patch("dnscf.time.sleep") as sleep_mock:
            dnscf.get_cf_speed_test_ip(max_retries=4)
        assert [c.args[0] for c in sleep_mock.call_args_list] == [1, 2, 4]

    def test_success_after_retry(self):
        bad = mock.Mock(status_code=500, text="")
        good = mock.Mock(status_code=200, text="1.2.3.4")
        with mock.patch("dnscf.httpx.get", side_effect=[bad, good]), \
             mock.patch("dnscf.time.sleep"):
            assert dnscf.get_cf_speed_test_ip() == "1.2.3.4"


# ---------- 客户端单例 ----------

class TestClientSingleton:
    def test_reuse_single_instance(self):
        with mock.patch.object(dnscf, "_client", None), \
             mock.patch.object(dnscf, "Cloudflare") as cf_mock:
            c1 = dnscf._get_client()
            c2 = dnscf._get_client()
            assert c1 is c2
            cf_mock.assert_called_once()


# ---------- main 退出码 ----------

class TestMainExitCode:
    def test_missing_env_returns_1(self):
        with mock.patch.object(dnscf, "CF_API_TOKEN", None):
            assert dnscf.main([]) == 1

    def test_fetch_failure_returns_1(self):
        env = mock.patch.multiple(dnscf, **ENV_OK)
        with env, mock.patch.object(dnscf, "get_cf_speed_test_ip", lambda *a, **k: None):
            assert dnscf.main([]) == 1

    def test_success_returns_0(self):
        env = mock.patch.multiple(dnscf, **ENV_OK)
        with env, \
             mock.patch.object(dnscf, "get_cf_speed_test_ip", lambda *a, **k: "1.2.3.4"), \
             mock.patch.object(dnscf, "parse_ip_addresses", lambda s: ["1.2.3.4"]), \
             mock.patch.object(dnscf, "get_dns_records", return_value=[]), \
             mock.patch.object(dnscf, "add_dns_record", lambda n, ip: f"ip:{ip} 解析 {n} 成功"), \
             mock.patch.object(dnscf, "push_plus", lambda content: None):
            assert dnscf.main([]) == 0

    def test_operation_failure_returns_1(self):
        def boom(name, ip):
            raise RuntimeError("模拟 API 错误")

        env = mock.patch.multiple(dnscf, **ENV_OK)
        with env, \
             mock.patch.object(dnscf, "get_cf_speed_test_ip", lambda *a, **k: "1.2.3.4"), \
             mock.patch.object(dnscf, "parse_ip_addresses", lambda s: ["1.2.3.4"]), \
             mock.patch.object(dnscf, "get_dns_records", return_value=[]), \
             mock.patch.object(dnscf, "add_dns_record", boom), \
             mock.patch.object(dnscf, "push_plus", lambda content: None):
            assert dnscf.main([]) == 1

    def test_dry_run_no_mutation(self):
        env = mock.patch.multiple(dnscf, **ENV_OK)
        with env, \
             mock.patch.object(dnscf, "get_cf_speed_test_ip", lambda *a, **k: "1.2.3.4"), \
             mock.patch.object(dnscf, "parse_ip_addresses", lambda s: ["1.2.3.4", "4.4.4.4"]), \
             mock.patch.object(dnscf, "get_dns_records", return_value=EXISTING_RECORDS), \
             mock.patch.object(dnscf, "add_dns_record") as add_mock, \
             mock.patch.object(dnscf, "del_dns_record") as del_mock, \
             mock.patch.object(dnscf, "push_plus") as push_mock:
            assert dnscf.main(["--dry-run"]) == 0
        # 演练模式不产生任何写操作与推送
        add_mock.assert_not_called()
        del_mock.assert_not_called()
        push_mock.assert_not_called()
