"""F2 只读审计（wayfinder 地图 #3 / 子票 #4）。

遍历受管记录名下的 A/AAAA 记录，汇总：
- 受管记录总数
- 类型与内容地址族错配的条数与明细（例如 A 记录里存着 IPv6）
- 内容无法解析为 IP 的条数（历史遗留数据的另一面）
- 同名同类型同内容的重复组数与明细（顺带回答 F3 在生产里的实际规模）

只读：仅调用 ``CloudflareRecordStore.list_records``（GET），不写入、不删除、不改 DNS。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe .scratch\\f2_audit.py

凭据：仓库根目录的 .env，由 python-dotenv 加载；脚本不打印任何凭据值。
退出码：0 成功；2 缺少环境变量。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cf_yx_dns import config, ips  # noqa: E402
from cf_yx_dns.cf_record_store import CloudflareRecordStore  # noqa: E402


def classify(records):
    """逐条判定「记录类型与内容地址族是否一致」，并按身份键统计重复。

    Args:
        records: ManagedRecord 列表

    Returns:
        tuple[list[dict], dict[tuple[str, str], int]]: (逐条判定, 重复键 -> 条数)
    """
    rows = []
    for record in records:
        addr = ips.parse_ip(record.content)
        expected = ips.record_type(addr) if addr is not None else None
        rows.append(
            {
                "id": record.id,
                "type": record.type,
                "content": record.content,
                "expected": expected,
                "mismatch": expected is not None and expected != record.type,
                "unparsable": addr is None,
            }
        )
    keys = Counter((row["type"], row["content"]) for row in rows)
    duplicates = {key: count for key, count in keys.items() if count > 1}
    return rows, duplicates


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    token = config.cf_api_token()
    zone = config.cf_zone_id()
    name = config.cf_dns_name()
    missing = [
        key
        for key, value in (
            ("CF_API_TOKEN", token),
            ("CF_ZONE_ID", zone),
            ("CF_DNS_NAME", name),
        )
        if not value
    ]
    if missing:
        print("缺少环境变量: " + ", ".join(missing))
        return 2

    store = CloudflareRecordStore(api_token=token, zone_id=zone)
    records = store.list_records(name)
    rows, duplicates = classify(records)
    mismatches = [row for row in rows if row["mismatch"]]
    unparsable = [row for row in rows if row["unparsable"]]

    print()
    print("=== 汇总 ===")
    print(f"受管记录总数: {len(rows)}")
    print(f"错配条数: {len(mismatches)}")
    print(f"内容无法解析为 IP 的条数: {len(unparsable)}")
    print(f"重复组数: {len(duplicates)}")

    if mismatches:
        print("--- 错配明细（记录 id / type / content / 按地址族应有的 type）---")
        for row in mismatches:
            print(f"{row['id']}  {row['type']}  {row['content']}  应为 {row['expected']}")
    if unparsable:
        print("--- 非 IP 内容明细 ---")
        for row in unparsable:
            print(f"{row['id']}  {row['type']}  {row['content']!r}")
    if duplicates:
        print("--- 重复组明细（type content 条数）---")
        for (record_type, content), count in duplicates.items():
            print(f"{record_type}  {content}  x{count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
