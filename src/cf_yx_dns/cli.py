"""命令行入口。

退出码约定：
- 0：成功；--dry-run 差量算出后恒为 0；--check 无差量时为 0；没有待推送内容时不推送
- 1：前置条件缺失、来源抓取/解析失败、存在 DNS 操作失败、--check 检测到差量、--notify-failure 推送失败
- 2：--dry-run 与 --check 并传（argparse 用法错误）
"""

import argparse
import sys

from dotenv import load_dotenv

from . import cf_record_store, cf_records, config, ips, notify


def build_parser():
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="Cloudflare DNS 优选 IP 自动更新工具")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：只打印将要新增/删除的记录，不修改 DNS（差量算出后恒以 0 退出）",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="检查模式：存在差量时以 1 退出，无差量以 0 退出，不修改 DNS",
    )
    parser.add_argument(
        "--notify-failure",
        metavar="REASON",
        default=None,
        help="只发送一条失败告警推送后退出（供工作流失败分支调用）",
    )
    return parser


def _notify_failure(reason):
    """发送失败告警；未配置 Token 时跳过并视为成功。

    Args:
        reason: 失败原因描述

    Returns:
        int: 退出码
    """
    content = notify.failure_content(reason)
    print(content)
    result = notify.push_plus(content, title=notify.FAILURE_TITLE)
    return 0 if result in (True, None) else 1


def main(argv=None):
    """命令行主流程。

    Args:
        argv: 参数列表（默认取 sys.argv[1:]）

    Returns:
        int: 退出码
    """
    args = build_parser().parse_args(argv)
    load_dotenv()

    if args.notify_failure is not None:
        return _notify_failure(args.notify_failure)

    required = {
        "CF_API_TOKEN": config.cf_api_token(),
        "CF_ZONE_ID": config.cf_zone_id(),
        "CF_DNS_NAME": config.cf_dns_name(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"错误: 缺少必要的环境变量 ({', '.join(missing)})", file=sys.stderr)
        return 1

    name = config.cf_dns_name()
    print(f"同步目标记录名: {name}（Zone: {config.cf_zone_id()}）")

    # 组合根：记录存储只在这里创建一次，两个同步动词都接收它作为显式依赖。
    # 构造期读取 Zone 与 Token，之后各操作不再各自去读环境变量。
    store = cf_record_store.CloudflareRecordStore(
        api_token=config.cf_api_token(),
        zone_id=config.cf_zone_id(),
    )

    page_text = ips.fetch_ip_source()
    if not page_text:
        print("错误: 无法获取优选 IP", file=sys.stderr)
        return 1

    target_ips = ips.parse_ip_addresses(page_text)
    if not target_ips:
        print("错误: 未解析到有效 IP 地址", file=sys.stderr)
        return 1

    for index, ip in enumerate(target_ips):
        print(f"优选 IP {index}: {ip}")

    try:
        plan = cf_records.plan_sync(name, target_ips, store)
    except cf_records.DnsSyncError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"获取现有 DNS 记录失败: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[演练模式] 将新增 {len(plan.to_create)} 条: {plan.to_create}")
        print(f"[演练模式] 将删除 {len(plan.to_delete)} 条: {[record.content for record in plan.to_delete]}")
        return 0

    if args.check:
        if plan.is_empty:
            print("[检查模式] 无差量")
            return 0
        print(f"[检查模式] 检测到差量: 新增 {len(plan.to_create)} 条，待删除 {len(plan.to_delete)} 条")
        return 1

    messages = cf_records.apply_sync(name, plan, store)
    if messages:
        print(messages)
        notify.push_plus("\n".join(messages))

    errors = [message for message in messages if message.startswith("失败: ")]
    if errors:
        print(f"错误: 存在 {len(errors)} 项 DNS 操作失败", file=sys.stderr)
        return 1
    return 0
