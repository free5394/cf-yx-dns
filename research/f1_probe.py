"""F1 写探针（wayfinder 地图 #3 / 子票 #6）。

在专用测试记录名下一次跑完两个问题：

1. **类型与内容地址族错配**：``type=A`` + IPv6 内容，Cloudflare API 是拒绝还是照收？
   （官方文档与 OpenAPI schema 只到「规范上不允许」，没有运行时错误条款，见
   ``research/f1-api-record-type-validation.md``。）
2. **同名同类型同内容的重复创建**：同一条 A 记录连续创建两次，第二次是「已存在」
   错误（81057 / 81058 一类），还是真的存出两条？（#5 结案依据只来自 TXT 场景的
   certbot #10725，A 记录没人验证过；#9 的判据依赖这一条。）

会话上限：只碰 ``--name`` 指定的专用记录名。运行前要求该名下没有任何 A/AAAA 记录；
运行结束（含异常）把该名下本次出现的记录全部删除。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe research\\f1_probe.py --name f1-probe.<zone 内子域> --yes

凭据：仓库根目录的 .env，由 python-dotenv 加载；脚本不打印任何凭据值。
退出码：0 成功；2 缺环境变量；3 未带 --yes；4 名下已有记录（为避免误删，拒绝运行）；
        5 清理未完成。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloudflare import APIError, Cloudflare  # noqa: E402

from cf_yx_dns import config  # noqa: E402

PROBE_PREFIX = "f1-probe."
MISMATCH_CONTENT = "2001:db8::1"  # IPv6 字面量，配 type=A 故意错配
DUPLICATE_CONTENT = "198.51.100.10"  # IPv4 字面量，配 type=A 是正常记录
COMMENT = "f1-probe"


def _list_records(client, zone_id, name):
    """列出该记录名下所有类型的记录（用于前置检查与清理）。"""
    return list(client.dns.records.list(zone_id=zone_id, name={"exact": name}))


def _create(client, zone_id, name, record_type, content):
    """发一次创建请求，打印请求与响应；被拒时返回 (None, 异常)，成功返回 (id, None)。"""
    payload = {"type": record_type, "name": name, "content": content, "ttl": 60, "proxied": False}
    print(f"请求: POST /zones/{zone_id}/dns_records {json.dumps(payload, ensure_ascii=False)}")
    try:
        record = client.dns.records.create(
            zone_id=zone_id,
            name=name,
            type=record_type,
            content=content,
            ttl=60,
            proxied=False,
            comment=COMMENT,
        )
    except APIError as error:
        body = json.dumps(error.body, ensure_ascii=False) if error.body is not None else ""
        print(f"响应: HTTP {error.status_code} {body}")
        return None, error
    print(f"响应: 成功 id={record.id} type={record.type} content={record.content}")
    return record.id, None


def _cleanup(client, zone_id, name):
    """删掉该记录名下的所有记录；返回失败条数。"""
    failures = 0
    for record in _list_records(client, zone_id, name):
        try:
            client.dns.records.delete(zone_id=zone_id, dns_record_id=record.id)
            print(f"清理: 已删除 {record.type} {record.content}（id={record.id}）")
        except APIError as error:
            failures += 1
            print(f"清理失败: id={record.id} HTTP {error.status_code} {error}")
    return failures


def main():
    parser = argparse.ArgumentParser(description="F1 错配与重复创建的写探针")
    parser.add_argument("--name", required=True, help=f"专用测试记录名，必须以 {PROBE_PREFIX} 开头")
    parser.add_argument("--yes", action="store_true", help="确认会向生产 zone 写入本次测试记录")
    args = parser.parse_args()

    if not args.name.startswith(PROBE_PREFIX):
        print(f"拒绝：--name 必须以 {PROBE_PREFIX} 开头，避免误写生产记录")
        return 4

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    token = config.cf_api_token()
    zone = config.cf_zone_id()
    missing = [key for key, value in (("CF_API_TOKEN", token), ("CF_ZONE_ID", zone)) if not value]
    if missing:
        print("缺少环境变量: " + ", ".join(missing))
        return 2

    client = Cloudflare(api_token=token)

    existing = _list_records(client, zone, args.name)
    if existing:
        print(f"拒绝：{args.name} 名下已有 {len(existing)} 条记录，为避免误删不运行")
        for record in existing:
            print(f"  {record.id}  {record.type}  {record.content}")
        return 4

    if not args.yes:
        print(f"计划：在 {args.name} 上依次做三件事，结束即删除")
        print(f"  1. type=A + IPv6（{MISMATCH_CONTENT}）→ 看拒绝还是照收")
        print(f"  2. type=A + IPv4（{DUPLICATE_CONTENT}）→ 建第一条")
        print("  3. 同一条 payload 再建一次 → 看是报错还是存出两条")
        print("确认写入请加 --yes")
        return 3

    print(f"=== 实验记录名：{args.name} ===")
    mismatch_id = None
    duplicate_error = None
    first_id = None
    second_id = None

    print()
    print("--- 1. 错配：type=A + IPv6 内容 ---")
    mismatch_id, mismatch_error = _create(client, zone, args.name, "A", MISMATCH_CONTENT)
    mismatch_status = "照收" if mismatch_id else f"拒绝（HTTP {mismatch_error.status_code}）"

    print()
    print("--- 2. 正常：type=A + IPv4 内容（第一条）---")
    first_id, first_error = _create(client, zone, args.name, "A", DUPLICATE_CONTENT)
    first_status = "成功" if first_id else f"失败（HTTP {first_error.status_code}）"

    print()
    print("--- 3. 重复：同一条 A 记录再建一次 ---")
    if first_id:
        second_id, duplicate_error = _create(client, zone, args.name, "A", DUPLICATE_CONTENT)
        duplicate_status = "报错拒绝" if second_id is None else "存出第二条"
    else:
        duplicate_status = "未执行（第一条没建成）"

    print()
    print("--- 清理 ---")
    cleanup_failures = _cleanup(client, zone, args.name)

    print()
    print("=== 汇总 ===")
    print(f"错配（A + IPv6）：{mismatch_status}")
    print(f"正常记录（A + IPv4）第一条：{first_status}")
    print(f"重复创建（同 A 同内容）：{duplicate_status}")
    if duplicate_error is not None:
        errors = (duplicate_error.body or {}).get("errors") or []
        codes = ", ".join(str(item.get("code")) for item in errors)
        print(f"重复创建的错误码：{codes or '（响应里没有 errors[]）'}")
    print(f"清理失败条数：{cleanup_failures}")
    print(f"记录名当前剩余记录数：{len(_list_records(client, zone, args.name))}")

    return 5 if cleanup_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
