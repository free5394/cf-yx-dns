"""PushPlus 推送。

推送结果分三种：成功、失败、未配置 Token（跳过）。调用方据此决定是否影响退出码。
"""

import httpx

from . import config

PUSHPLUS_URL = "https://www.pushplus.plus/send"
SUCCESS_TITLE = "IP优选DNSCF推送"
FAILURE_TITLE = "cf-yx-dns DNS 更新失败"


def _json_body(response):
    """尽力解析响应 JSON，非法 JSON 返回 None。"""
    try:
        return response.json()
    except Exception:
        return None


def push_plus(content, *, title=SUCCESS_TITLE, token=None):
    """发送 PushPlus 消息。

    Args:
        content: 消息正文（markdown）
        title: 消息标题
        token: 覆盖配置里的 Token（默认读 PUSHPLUS_TOKEN）

    Returns:
        bool | None: True 发送成功；False 发送失败；None 未配置 Token（跳过）
    """
    token = token if token is not None else config.pushplus_token()
    if not token:
        print("PUSHPLUS_TOKEN 未设置，跳过消息推送")
        return None

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
        "channel": "wechat",
    }
    try:
        response = httpx.post(PUSHPLUS_URL, json=payload, timeout=config.DEFAULT_TIMEOUT)
    except Exception as e:
        print(f"消息推送失败: {e}")
        return False

    if response.status_code != 200:
        print(f"消息推送失败: HTTP {response.status_code}")
        return False

    body = _json_body(response)
    if body is None:
        print("消息推送失败: 响应不是合法 JSON")
        return False

    if str(body.get("code")) != "200":
        print(f"消息推送失败: code={body.get('code')} msg={body.get('msg')}")
        return False

    print("消息推送成功")
    return True


def failure_content(reason):
    """构造失败告警正文。

    告警正文由脚本侧生成，工作流只负责在失败分支调用脚本，避免同一条推送逻辑
    在 YAML 与 Python 里各写一份、标题与模板逐渐漂移。

    Args:
        reason: 失败原因描述

    Returns:
        str: markdown 正文
    """
    lines = [
        "## DNS 更新任务失败",
        "",
        f"- 记录名: {config.cf_dns_name() or '(未设置 CF_DNS_NAME)'}",
        f"- Zone: {config.cf_zone_id() or '(未设置 CF_ZONE_ID)'}",
        f"- 原因: {reason}",
    ]
    return "\n".join(lines)
