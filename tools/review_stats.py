"""统计 ``reviews/`` 下的复审报告（返工轮次与口径不符次数两个分子）。

约定见 ``docs/agents/code-review.md``。本脚本同时是「报告合格」的机械闸门：缺元信息、
判定值不在词表、finding 表结构不合法、目录为空时都会报错并以退出码 1 结束，不静默跳过 ——
这两个指标当初失效的原因正是「采集入口不可执行」。

用法：``uv run python tools/review_stats.py [reviews 目录或报告文件 ...]``，默认 ``reviews``；
``--since YYYYMMDD`` 只统计文件名日期不早于该日的报告。沙箱里 ``uv`` 可能建不了缓存，
可改用 ``.venv/Scripts/python.exe tools/review_stats.py``。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 正文里的四行元信息（``- 标签：值``），缺一个就判不合格。
META_LABELS = ("对象提交", "评审范围", "评审者", "判定")
# 判定词表；后两个计入「返工轮次」分子（口径乙：复审-打回事件数）。
VERDICTS = ("通过", "打回", "拒绝")
REWORK_VERDICTS = ("打回", "拒绝")
# finding 表的列名与严重度词表。
FINDING_COLUMNS = ("编号", "严重度", "类别", "位置", "问题", "处置")
SEVERITIES = ("P0", "P1", "P2", "P3")
MISMATCH_CATEGORY = "口径不符"
FINDING_HEADING = "逐条 finding"
# 文件名：YYYYMMDD-<对象提交短哈希>[-rN].md
FILE_NAME = re.compile(r"^(?P<date>\d{8})-(?P<commit>[0-9a-f]{7,40})(?:-r\d+)?\.md$")
COMMIT_HASH = re.compile(r"[0-9a-f]{7,40}")
META_LINE = re.compile(r"^-\s*(?P<label>[^：:]{1,12})[：:]\s*(?P<value>.+?)\s*$")
SEPARATOR_CELLS = set("-: ")
DEFAULT_ROOT = "reviews"
CONVENTION = "docs/agents/code-review.md"


@dataclass(frozen=True)
class Report:
    """一份复审报告：元信息、finding 清单与不合格项（problems 为空才算合格）。"""

    path: Path
    meta: dict = field(default_factory=dict)
    findings: tuple = ()
    problems: tuple = ()


def _clean(value):
    """去掉值两侧的空白与反引号。"""
    return value.strip().strip("`").strip()


def file_date(path):
    """从文件名取出日期（YYYYMMDD）；文件名不符合约定时返回 None。"""
    match = FILE_NAME.match(Path(path).name)
    return match.group("date") if match else None


def _parse_findings(text, path):
    """解析「逐条 finding」小节的表格，返回 (findings, problems)。"""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lstrip("#").strip() == FINDING_HEADING:
            start = index + 1
            break
    if start is None:
        return (), (f"{path}: 找不到「{FINDING_HEADING}」小节",)

    problems = []
    rows = []
    header_seen = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not header_seen:
                header_seen = True
                if tuple(cells) != FINDING_COLUMNS:
                    problems.append(f"{path}: finding 表头是 {cells}，应为 {list(FINDING_COLUMNS)}")
                continue
            if all(set(cell) <= SEPARATOR_CELLS for cell in cells):
                continue
            if len(cells) != len(FINDING_COLUMNS):
                problems.append(f"{path}: finding 行有 {len(cells)} 列（应为 {len(FINDING_COLUMNS)} 列）：{stripped}")
                continue
            rows.append(dict(zip(FINDING_COLUMNS, cells, strict=True)))
        elif header_seen and stripped:
            break
    if not header_seen:
        problems.append(f"{path}: 「{FINDING_HEADING}」小节里没有 finding 表")
    for row in rows:
        if row["严重度"] not in SEVERITIES:
            allowed = " / ".join(SEVERITIES)
            problems.append(f"{path}: finding {row['编号']} 的严重度「{row['严重度']}」不在词表（{allowed}）")
        if not row["类别"]:
            problems.append(f"{path}: finding {row['编号']} 缺「类别」")
    return tuple(rows), tuple(problems)


def parse_report(text, path):
    """解析一份报告的正文，返回 Report（problems 里是全部不合格项）。"""
    path = Path(path)
    problems = []
    meta = {}
    for line in text.splitlines():
        match = META_LINE.match(line)
        if match and match.group("label") in META_LABELS and match.group("label") not in meta:
            meta[match.group("label")] = _clean(match.group("value"))

    for label in META_LABELS:
        if label not in meta:
            problems.append(f"{path}: 缺少元信息「{label}」")
    if "对象提交" in meta and not COMMIT_HASH.search(meta["对象提交"]):
        problems.append(f"{path}: 对象提交「{meta['对象提交']}」里没有 7 位以上的十六进制提交号")
    if "评审范围" in meta and ".." not in meta["评审范围"]:
        problems.append(f"{path}: 评审范围「{meta['评审范围']}」不是「基点..对象提交」形式")
    if "判定" in meta and meta["判定"] not in VERDICTS:
        problems.append(f"{path}: 判定「{meta['判定']}」不在词表（{' / '.join(VERDICTS)}）")

    findings, table_problems = _parse_findings(text, path)
    problems.extend(table_problems)
    return Report(path=path, meta=meta, findings=findings, problems=tuple(problems))


def collect_reports(paths, since=None):
    """按路径收集报告，返回 (reports, problems)。

    problems 只放路径层的问题（路径不存在、目录里没有报告、文件名不合约定）；
    报告自身的问题挂在 Report.problems 上，用 all_problems 合并。
    """
    reports = []
    problems = []
    for target in paths:
        target = Path(target)
        if target.is_dir():
            files = sorted(target.glob("*.md"))
            if not files:
                problems.append(f"{target}: 目录里没有 .md 报告（归档约定见 {CONVENTION}）")
                continue
        elif target.is_file():
            files = [target]
        else:
            problems.append(f"{target}: 归档路径不存在（归档约定见 {CONVENTION}）")
            continue
        for path in files:
            date = file_date(path)
            if date is None:
                problems.append(f"{path}: 文件名不符合「YYYYMMDD-<对象提交短哈希>.md」（同日复评加 -rN）")
                continue
            if since is not None and date < since:
                continue
            reports.append(parse_report(path.read_text(encoding="utf-8"), path))
    return reports, problems


def all_problems(reports, path_problems):
    """路径层问题 + 每份报告自身的问题，按报告顺序并列。"""
    problems = list(path_problems)
    for report in reports:
        problems.extend(report.problems)
    return problems


def summarize(reports):
    """按固定口径汇总：返工轮次看判定，口径不符次数看 finding 的类别列。"""
    severity_counts = {}
    mismatch = 0
    for report in reports:
        for finding in report.findings:
            if finding["类别"] == MISMATCH_CATEGORY:
                mismatch += 1
            severity = finding["严重度"]
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return {
        "reports": len(reports),
        "rework_numerator": sum(1 for report in reports if report.meta.get("判定") in REWORK_VERDICTS),
        "findings_total": sum(len(report.findings) for report in reports),
        "mismatch_numerator": mismatch,
        "severity_counts": severity_counts,
    }


def format_summary(reports, summary, window):
    """把报告与汇总渲染成可粘贴的文本。"""
    lines = [
        f"复审报告 {summary['reports']} 份（窗口：{window}）",
        f"  返工轮次分子 = {summary['rework_numerator']} ｜ 分母 = {summary['reports']} 份报告"
        f"（口径乙：判定为「{'」「'.join(REWORK_VERDICTS)}」的报告份数）",
        f"  口径不符 finding 数 = {summary['mismatch_numerator']}"
        f" ｜ 分母 = {summary['findings_total']} 条 finding（{MISMATCH_CATEGORY} 类）",
        "  明细：",
    ]
    if not reports:
        lines.append("    （没有报告；归档约定见 " + CONVENTION + "）")
    for report in reports:
        severity = " ".join(f"{name}×{summary['severity_counts'].get(name, 0)}" for name in SEVERITIES)
        mismatch = sum(1 for finding in report.findings if finding["类别"] == MISMATCH_CATEGORY)
        lines.append(
            f"    {report.path.name}  判定={report.meta.get('判定', '缺')}"
            f"  对象提交={report.meta.get('对象提交', '缺')}"
            f"  finding {len(report.findings)} 条（其中 {MISMATCH_CATEGORY} {mismatch}；{severity}）"
        )
    return "\n".join(lines)


def main(argv):
    """返回退出码：0 全部合格，1 有不合格项或归档不可用。"""
    args = list(argv[1:])
    since = None
    if "--since" in args:
        index = args.index("--since")
        if index + 1 >= len(args):
            print("--since 后面要跟 YYYYMMDD 日期")
            return 1
        since = args[index + 1]
        del args[index : index + 2]
        if not re.fullmatch(r"\d{8}", since):
            print(f"--since 需要 YYYYMMDD 形式，收到「{since}」")
            return 1

    paths = args or [DEFAULT_ROOT]
    reports, path_problems = collect_reports(paths, since)
    problems = all_problems(reports, path_problems)
    print(format_summary(reports, summarize(reports), f"{since} 起" if since else "全部"))
    if problems:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        print(f"\n以下报告不合格（{len(problems)} 项）：\n{detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
