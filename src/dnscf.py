#!/usr/bin/env python3
"""兼容入口：``uv run src/dnscf.py`` 等价于 ``PYTHONPATH=src python -m cf_yx_dns``。

实现已拆分到 ``src/cf_yx_dns/`` 包；保留这个文件是为了让 README 与工作流里的
命令继续可用（脚本所在目录会进入 sys.path，因此可以直接 import 同级的包）。
"""

import sys

from cf_yx_dns.cli import main

if __name__ == "__main__":
    sys.exit(main())
