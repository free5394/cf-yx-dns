"""语言纪律检查脚本自身的测试。

脚本在 ``tools/`` 下（不属于 ``src`` 包），因此用 importlib 按路径加载，不改 pytest 配置。
用例只喂字符串、不落盘：沙箱里 pytest 的 ``tmp_path`` 用不了（系统临时目录能建目录但不能写文件）。
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_language.py"
_spec = importlib.util.spec_from_file_location("check_language", SCRIPT)
check_language = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_language)


class TestBannedWords:
    def test_flags_banned_word_from_glossary(self):
        problems = check_language.scan_source('"""把该域名的解析清空。"""\n')

        assert len(problems) == 1
        assert "域名" in problems[0]
        assert "记录名" in problems[0]

    def test_clean_source_has_no_problem(self):
        assert check_language.scan_source('"""把该记录名的解析清空。"""\n') == []


class TestChinesePrompts:
    def test_flags_english_print(self):
        problems = check_language.scan_source('print(f"dns add success: {1}")\n')

        assert len(problems) == 1
        assert "没有中文" in problems[0]

    def test_flags_english_raise(self):
        assert len(check_language.scan_source('raise RuntimeError(f"invalid {1}")\n')) == 1

    def test_chinese_print_and_non_literal_print_pass(self):
        source = 'print(f"新增成功: {1}")\nprint(value)\nraise RuntimeError("非法输入")\n'

        assert check_language.scan_source(source) == []


class TestMain:
    def test_exit_code_and_report_when_any_file_has_problem(self, monkeypatch, capsys):
        monkeypatch.setattr(check_language, "collect_targets", lambda roots: [Path("sample.py")])
        monkeypatch.setattr(check_language, "check_source", lambda path: ["sample.py:1: 问题"])

        assert check_language.main(["check_language", "src"]) == 1
        assert "问题" in capsys.readouterr().out

    def test_exit_code_zero_when_nothing_is_reported(self, monkeypatch, capsys):
        monkeypatch.setattr(check_language, "collect_targets", lambda roots: [Path("sample.py")])
        monkeypatch.setattr(check_language, "check_source", lambda path: [])

        assert check_language.main(["check_language", "src"]) == 0
        assert "通过" in capsys.readouterr().out


class TestRepositoryIsClean:
    """仓库自身的实现与工具脚本必须通过检查。

    只扫 ``src`` 与 ``tools``：测试目录里本来就要把「错词」写成样例数据（见上面的用例），
    把它们也扫进来会立刻自相矛盾。
    """

    REPO_ROOT = Path(__file__).resolve().parents[1]

    def test_src_and_tools_pass(self, capsys):
        exit_code = check_language.main(
            ["check_language", str(self.REPO_ROOT / "src"), str(self.REPO_ROOT / "tools")]
        )

        assert exit_code == 0, capsys.readouterr().out
