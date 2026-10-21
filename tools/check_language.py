"""检查本仓库的语言纪律（只做机械可判的两类）。

规则来源：

- ``GLOSSARY.md`` 里用「_避免_」明确列出的错词：**记录名**（避免「域名」）、**优选 IP**（避免「优质 IP」）。
  词表里其余的「避免」词（节点、线路）与「首次出现标注中文释义」是判断项，不在本脚本检查范围。
- ``~/.dsh/AGENTS.md`` 偏好 3：代码注释、错误提示信息、项目文档使用简体中文。本脚本只查
  ``print(...)`` 与 ``raise ...(...)`` 的字符串字面量里有没有中文字符，不做语义判断。

用法：``uv run python tools/check_language.py [路径 ...]``，默认只扫 ``src``；有命中时退出码 1。
它同时由 ``tests/test_check_language.py`` 覆盖，所以跑 pytest 就等于跑这道检查。
沙箱里 ``uv`` 可能建不了自己的缓存（os error 5），可改用 ``.venv/Scripts/python.exe tools/check_language.py``。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# 词表明确列为「避免」的错词 → 该用的词
BANNED_WORDS = (("域名", "记录名"), ("优质 IP", "优选 IP"))
# 中文字符（含扩展区之外的常用汉字）
CJK = re.compile(r"[\u4e00-\u9fff]")
DEFAULT_ROOTS = ("src",)
SCRIPT_PATH = Path(__file__).resolve()


def _first_string_literal(call: ast.Call):
    """调用参数里第一段字符串字面量；f-string 取其中的字面部分。"""
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            return "".join(
                part.value
                for part in arg.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
    return None


def scan_source(text, path="<memory>"):
    """扫描一段源码文本，返回违规说明列表（空列表表示通过）。"""
    problems = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for banned, preferred in BANNED_WORDS:
            if banned in line:
                problems.append(f"{path}:{lineno}: 词表禁用「{banned}」，应写「{preferred}」")
    for node in ast.walk(ast.parse(text)):
        call = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            call = node
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            call = node.exc
        if call is None:
            continue
        literal = _first_string_literal(call)
        if literal is not None and not CJK.search(literal):
            problems.append(f"{path}:{call.lineno}: print/raise 的提示语里没有中文，改用简体中文")
    return problems


def check_source(path):
    """读取并检查一个 .py 文件，返回违规说明列表（空列表表示通过）。"""
    return scan_source(path.read_text(encoding="utf-8"), path)


def collect_targets(roots):
    """把目录展开成 .py 文件清单（跳过本脚本自身：它的 docstring 里要举错词当例子）。"""
    targets = []
    for root in roots:
        found = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        targets.extend(p for p in found if p.resolve() != SCRIPT_PATH)
    return targets


def main(argv):
    """返回退出码：0 通过，1 有命中。"""
    roots = [Path(arg) for arg in argv[1:]] or [Path(root) for root in DEFAULT_ROOTS]
    targets = collect_targets(roots)
    problems = [problem for target in targets for problem in check_source(target)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n共 {len(problems)} 处语言纪律问题（规则来源见本脚本 docstring）。")
        return 1
    print(f"语言纪律检查通过（{len(targets)} 个文件）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
