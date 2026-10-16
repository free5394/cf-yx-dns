"""命令行入口的测试：退出码、演练/检查模式、失败上报。"""

import pytest

from cf_yx_dns import cf_records, cli

NAME = "dns.example.com"


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """仓库根目录存在 .env，测试统一屏蔽，避免真实配置泄漏进断言。"""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "token")
    monkeypatch.setenv("CF_ZONE_ID", "zone-id")
    monkeypatch.setenv("CF_DNS_NAME", NAME)
    monkeypatch.setenv("PUSHPLUS_TOKEN", "pp-token")


def stub_source(monkeypatch, target_ips=("104.16.1.1",)):
    monkeypatch.setattr(cli.ips, "fetch_ip_source", lambda: "<html>page</html>")
    monkeypatch.setattr(cli.ips, "parse_ip_addresses", lambda page_text: list(target_ips) or None)


def stub_plan(monkeypatch, plan):
    monkeypatch.setattr(cli.cf_records, "plan_sync", lambda name, target_ips: plan)


def make_plan(to_create=(), to_delete=()):
    return cf_records.SyncPlan(to_create=list(to_create), to_delete=list(to_delete), proxied_by_type={})


class TestPreconditions:
    def test_missing_env_returns_1(self, monkeypatch, capsys):
        for name in ("CF_API_TOKEN", "CF_ZONE_ID", "CF_DNS_NAME"):
            monkeypatch.delenv(name, raising=False)

        assert cli.main([]) == 1
        assert "缺少必要的环境变量" in capsys.readouterr().err

    def test_fetch_failure_returns_1(self, monkeypatch, env, capsys):
        monkeypatch.setattr(cli.ips, "fetch_ip_source", lambda: None)

        assert cli.main([]) == 1
        assert "无法获取优选 IP" in capsys.readouterr().err

    def test_parse_failure_returns_1(self, monkeypatch, env, capsys):
        stub_source(monkeypatch, target_ips=())

        assert cli.main([]) == 1
        assert "未解析到有效 IP 地址" in capsys.readouterr().err

    def test_plan_failure_returns_1(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        monkeypatch.setattr(cli.cf_records, "plan_sync", _raise_api_error)

        assert cli.main([]) == 1
        assert "获取现有 DNS 记录失败" in capsys.readouterr().err

    def test_empty_target_guard_returns_1(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        monkeypatch.setattr(cli.cf_records, "plan_sync", _raise_sync_error)

        assert cli.main([]) == 1
        assert "拒绝同步" in capsys.readouterr().err


def _raise_api_error(name, target_ips):
    raise RuntimeError("api down")


def _raise_sync_error(name, target_ips):
    raise cf_records.DnsSyncError("目标 IP 列表为空，拒绝同步")


class TestDryRun:
    def test_prints_plan_without_writing_or_pushing(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan(["104.16.2.2"], [{"content": "104.16.9.9"}]))
        monkeypatch.setattr(cli.cf_records, "apply_sync", _forbid_call)
        monkeypatch.setattr(cli.notify, "push_plus", _forbid_push)

        assert cli.main(["--dry-run"]) == 0

        out = capsys.readouterr().out
        assert "[演练模式] 将新增 1 条" in out
        assert "[演练模式] 将删除 1 条" in out

    def test_dry_run_without_diff_still_exits_0(self, monkeypatch, env):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan())
        monkeypatch.setattr(cli.cf_records, "apply_sync", _forbid_call)

        assert cli.main(["--dry-run"]) == 0


def _forbid_call(*args, **kwargs):
    pytest.fail("演练/检查模式不应执行写操作")


def _forbid_push(*args, **kwargs):
    pytest.fail("演练/检查模式不应推送")


class TestCheck:
    def test_drift_returns_1_without_writing(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan(["104.16.2.2"]))
        monkeypatch.setattr(cli.cf_records, "apply_sync", _forbid_call)
        monkeypatch.setattr(cli.notify, "push_plus", _forbid_push)

        assert cli.main(["--check"]) == 1
        assert "检测到差量" in capsys.readouterr().out

    def test_no_drift_returns_0(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan())
        monkeypatch.setattr(cli.cf_records, "apply_sync", _forbid_call)

        assert cli.main(["--check"]) == 0
        assert "无差量" in capsys.readouterr().out


class TestRealRun:
    def test_success_returns_0_and_pushes_summary(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan(["104.16.2.2"]))
        monkeypatch.setattr(
            cli.cf_records,
            "apply_sync",
            lambda name, plan: ([f"ip:104.16.2.2 解析 {name} 成功"], []),
        )
        pushed = {}
        monkeypatch.setattr(cli.notify, "push_plus", lambda content, **kwargs: pushed.update(content=content))

        assert cli.main([]) == 0
        assert "104.16.2.2 解析 dns.example.com 成功" in pushed["content"]
        assert "失败" not in pushed["content"]

    def test_operation_failure_returns_1_and_reports_failure(self, monkeypatch, env, capsys):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan(["104.16.2.2"]))
        monkeypatch.setattr(
            cli.cf_records,
            "apply_sync",
            lambda name, plan: ([], ["ip:104.16.2.2 新增 dns.example.com 失败 - api down"]),
        )
        pushed = {}
        monkeypatch.setattr(cli.notify, "push_plus", lambda content, **kwargs: pushed.update(content=content))

        assert cli.main([]) == 1

        assert "失败" in pushed["content"]
        assert "新增 dns.example.com 失败" in pushed["content"]
        assert "存在 1 项 DNS 操作失败" in capsys.readouterr().err

    def test_no_changes_skips_push(self, monkeypatch, env):
        stub_source(monkeypatch)
        stub_plan(monkeypatch, make_plan())
        monkeypatch.setattr(cli.cf_records, "apply_sync", lambda name, plan: ([], []))
        monkeypatch.setattr(cli.notify, "push_plus", _forbid_push)

        assert cli.main([]) == 0


class TestNotifyFailure:
    def test_sends_alert_and_returns_0(self, monkeypatch, env):
        sent = {}

        def fake_push(content, **kwargs):
            sent.update(content=content, **kwargs)
            return True

        monkeypatch.setattr(cli.notify, "push_plus", fake_push)

        assert cli.main(["--notify-failure", "抓取优选 IP 失败"]) == 0
        assert sent["title"] == cli.notify.FAILURE_TITLE
        assert "抓取优选 IP 失败" in sent["content"]
        assert NAME in sent["content"]

    def test_without_token_still_returns_0(self, monkeypatch, env):
        monkeypatch.delenv("PUSHPLUS_TOKEN")
        monkeypatch.setattr(cli.notify, "push_plus", lambda content, **kwargs: None)

        assert cli.main(["--notify-failure", "boom"]) == 0

    def test_push_failure_returns_1(self, monkeypatch, env):
        monkeypatch.setattr(cli.notify, "push_plus", lambda content, **kwargs: False)

        assert cli.main(["--notify-failure", "boom"]) == 1

    def test_does_not_require_cf_env(self, monkeypatch):
        for name in ("CF_API_TOKEN", "CF_ZONE_ID", "CF_DNS_NAME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(cli.notify, "push_plus", lambda content, **kwargs: True)

        assert cli.main(["--notify-failure", "boom"]) == 0
