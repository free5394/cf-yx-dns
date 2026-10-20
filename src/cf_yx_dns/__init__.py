"""cf-yx-dns：Cloudflare DNS 优选 IP 自动更新工具。

模块划分：
- ``config``：环境变量的唯一读取点；
- ``ips``：优选 IP 来源的抓取、候选提取、校验与规范化；
- ``cf_records``：DNS 记录的差量计算与同步（两个同步动词接收记录存储作为显式依赖）；
- ``cf_record_store``：记录存储的 Cloudflare 实现（唯一接触 SDK 的一层）；
- ``notify``：PushPlus 推送；
- ``cli``：命令行入口（记录存储的组合根）。
"""

__all__ = ["cf_record_store", "cf_records", "cli", "config", "ips", "notify"]
