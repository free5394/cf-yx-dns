"""cf-yx-dns：Cloudflare DNS 优选 IP 自动更新工具。

模块划分：
- ``config``：环境变量的唯一读取点；
- ``ips``：优选 IP 来源的抓取、候选提取、校验与规范化；
- ``cf_records``：Cloudflare DNS 记录的读取、差量计算与同步；
- ``notify``：PushPlus 推送；
- ``cli``：命令行入口。
"""

__all__ = ["cf_records", "cli", "config", "ips", "notify"]
