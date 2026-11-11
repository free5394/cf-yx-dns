"""复审报告统计脚本自身的测试。

脚本在 ``tools/`` 下（不属于 ``src`` 包），因此用 importlib 按路径加载，不改 pytest 配置。
用例尽量喂字符串、不落盘（沙箱里 ``tmp_path`` 不一定能写文件）。
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "review_stats.py"
_spec = importlib.util.spec_from_file_location("review_stats", SCRIPT)
review_stats = importlib.util.module_from_spec(_spec)
# 按路径加载的模块要先登记进 sys.modules：dataclass 会回头查 cls.__module__ 的模块字典。
sys.modules["review_stats"] = review_stats
_spec.loader.exec_module(review_stats)


GOOD_HEAD = """# 复审报告：abc1234 归档约定

- 对象提交：`abc1234def5678901234567890123456789012ab`
- 评审范围：`c571466..abc1234`
- 评审者：Lead（并行两个只读子代理，只回正文）
- 判定：打回
"""

GOOD_TABLE = """
## 逐条 finding

| 编号 | 严重度 | 类别 | 位置 | 问题 | 处置 |
| --- | --- | --- | --- | --- | --- |
| F1 | P1 | 口径不符 | `docs/agents/code-review.md:3` | 统计命令跑不通 | 已修 `fff1111` |
| F2 | P3 | 表述 | `tools/review_stats.py:9` | 措辞含混 | 已修 `fff1111` |
"""

JUDGEMENT_SECTION = "\n## 判定结论\n\n范围不通过，需按 finding 返工。\n"

GOOD_TEXT = GOOD_HEAD + GOOD_TABLE + JUDGEMENT_SECTION

FINDING_HEADER = "| 编号 | 严重度 | 类别 | 位置 | 问题 | 处置 |"
FINDING_SEPARATOR = "| --- | --- | --- | --- | --- | --- |"


def findings_table(*rows):
    """拼一张 finding 表；不传 rows 就是「表格存在但没有数据行」。"""
    return "\n".join((FINDING_HEADER, FINDING_SEPARATOR, *rows))


def parse(text=GOOD_TEXT, name="20261111-abc1234.md"):
    """按约定解析一段报告文本。"""
    return review_stats.parse_report(text, Path(name))


class TestParseReport:
    def test_meta_and_findings_are_parsed(self):
        report = parse()

        assert report.problems == ()
        assert report.meta["判定"] == "打回"
        assert report.meta["评审范围"] == "c571466..abc1234"
        assert [finding["严重度"] for finding in report.findings] == ["P1", "P3"]
        assert report.findings[0]["类别"] == "口径不符"

    def test_missing_meta_label_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("- 评审范围：`c571466..abc1234`\n", ""))

        assert any("评审范围" in problem for problem in report.problems)

    def test_verdict_outside_vocabulary_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("- 判定：打回", "- 判定：基本通过"))

        assert any("判定" in problem for problem in report.problems)

    def test_object_commit_without_hash_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("`abc1234def5678901234567890123456789012ab`", "见上文"))

        assert any("对象提交" in problem for problem in report.problems)

    def test_review_range_without_dots_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("`c571466..abc1234`", "只有对象提交"))

        assert any("评审范围" in problem for problem in report.problems)

    def test_short_object_commit_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("abc1234def5678901234567890123456789012ab", "abc1234"))

        assert any("40 位" in problem for problem in report.problems)

    def test_backticked_category_still_counts(self):
        report = parse(GOOD_TEXT.replace("| F1 | P1 | 口径不符 |", "| F1 | P1 | `口径不符` |"))

        assert report.problems == ()
        assert report.mismatch_count == 1

    def test_unknown_severity_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("| F1 | P1 |", "| F1 | 高 |"))

        assert any("严重度" in problem for problem in report.problems)

    def test_short_finding_row_is_a_problem(self):
        full_row = "| F2 | P3 | 表述 | `tools/review_stats.py:9` | 措辞含混 | 已修 `fff1111` |"
        report = parse(GOOD_TEXT.replace(full_row, "| F2 | P3 | 表述 |"))

        assert any("列" in problem for problem in report.problems)

    def test_missing_findings_table_is_a_problem(self):
        report = parse(GOOD_TEXT.split("## 逐条 finding")[0])

        assert any("finding" in problem for problem in report.problems)

    def test_missing_verdict_section_is_a_problem(self):
        report = parse(GOOD_TEXT.replace("## 判定结论", "## 结论"))

        assert any("判定结论" in problem for problem in report.problems)

    def test_report_with_no_finding_row_is_valid(self):
        report = parse(GOOD_HEAD + "\n## 逐条 finding\n\n" + findings_table() + JUDGEMENT_SECTION)

        assert report.problems == ()
        assert report.findings == ()


class TestFileName:
    def test_convention_name_is_accepted(self):
        assert review_stats.file_date(Path("reviews/20261111-abc1234.md")) == "20261111"

    def test_repeat_review_name_is_accepted(self):
        assert review_stats.file_date(Path("reviews/20261111-abc1234-r2.md")) == "20261111"

    def test_name_without_commit_is_rejected(self):
        assert review_stats.file_date(Path("reviews/复审报告.md")) is None


class TestSummarize:
    def test_numerators_come_from_verdict_and_category(self):
        rework = parse()
        passed_text = GOOD_HEAD.replace("- 判定：打回", "- 判定：通过")
        passed = parse(
            passed_text + "\n## 逐条 finding\n\n" + findings_table() + JUDGEMENT_SECTION, "20261111-bbb2222.md"
        )

        summary = review_stats.summarize([rework, passed])

        assert summary["reports"] == 2
        assert summary["rework_numerator"] == 1
        assert summary["findings_total"] == 2
        assert summary["mismatch_numerator"] == 1
        assert summary["severity_counts"] == {"P1": 1, "P3": 1}


class TestFormatSummary:
    def test_per_report_severity_is_not_the_total(self):
        first = parse()
        second_text = (
            GOOD_HEAD.replace("- 判定：打回", "- 判定：通过")
            + "\n## 逐条 finding\n\n"
            + findings_table("| F3 | P0 | 缺陷 | `x.md:1` | 会崩 | 已修 |")
            + JUDGEMENT_SECTION
        )
        second = parse(second_text, "20261111-bbb2222.md")

        text = review_stats.format_summary([first, second], review_stats.summarize([first, second]), "全部")

        first_line = next(line for line in text.splitlines() if "20261111-abc1234" in line)
        second_line = next(line for line in text.splitlines() if "20261111-bbb2222" in line)
        assert "P1×1" in first_line and "P0×0" in first_line
        assert "P0×1" in second_line and "P1×0" in second_line
        assert "严重度合计" in text


class TestMain:
    def test_exit_zero_and_reports_both_numerators(self, monkeypatch, capsys):
        monkeypatch.setattr(review_stats, "collect_reports", lambda paths, since=None: ([parse()], []))

        assert review_stats.main(["review_stats"]) == 0
        out = capsys.readouterr().out
        assert "返工轮次分子" in out
        assert "口径不符" in out

    def test_exit_one_when_a_report_is_malformed(self, monkeypatch, capsys):
        broken = parse(GOOD_TEXT.replace("- 判定：打回", "- 判定：基本通过"))
        monkeypatch.setattr(review_stats, "collect_reports", lambda paths, since=None: ([broken], []))

        assert review_stats.main(["review_stats"]) == 1
        assert "判定" in capsys.readouterr().out

    def test_exit_one_when_archive_is_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(
            review_stats, "collect_reports", lambda paths, since=None: ([], ["归档目录不存在：reviews"])
        )

        assert review_stats.main(["review_stats"]) == 1
        assert "归档目录不存在" in capsys.readouterr().out

