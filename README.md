# cf-yx-dns

Cloudflare DNS 优选 IP 自动更新工具

## 项目概述

cf-yx-dns 是一个用于自动获取 Cloudflare 优选 IP 并更新 DNS 记录的 Python 工具。通过定时任务自动执行，帮助用户快速将优质 IP 地址同步到 Cloudflare DNS，实现网络访问优化。

## 核心功能

- **自动获取优选 IP**：从第三方服务采集最新的 Cloudflare 优选 IP 列表
- **智能 DNS 管理**：差量比对现有记录，先新增缺失记录再删除多余记录，更新过程不中断解析
- **只管辖自己的记录**：仅处理同名下的 A/AAAA 记录，CNAME/TXT 等其他类型记录不读也不删；解析不到有效 IP 时拒绝执行任何删除
- **消息推送**：通过 PushPlus 发送微信推送通知，实时掌握执行状态
- **自动化执行**：支持 GitHub Actions 定时任务，实现无人值守运行

## 技术栈

| 组件          | 说明               |
| ------------- | ------------------ |
| Python        | 3.10+              |
| cloudflare    | Cloudflare API SDK |
| python-dotenv | 环境变量管理       |
| httpx         | HTTP 请求库        |
| uv            | Python 包管理工具  |

## 项目结构

```text
/
├── .github/
│   └── workflows/
│       ├── ci.yml          # CI：代码检查 + 测试
│       ├── dns_cf.yml      # DNS 更新工作流（每6小时执行）
│       └── sync.yml        # Fork 同步工作流
├── docs/
│   ├── adr/                # 架构决策记录
│   └── agents/             # 面向编码代理的说明
├── src/
│   ├── cf_yx_dns/          # 实现包
│   │   ├── cli.py          # 命令行入口（--dry-run / --check / --notify-failure）
│   │   ├── config.py       # 环境变量读取
│   │   ├── ips.py          # 优选 IP 抓取、提取与校验
│   │   ├── cf_records.py   # DNS 记录差量同步
│   │   └── notify.py       # PushPlus 推送
│   └── dnscf.py            # 兼容入口（等价于 PYTHONPATH=src python -m cf_yx_dns）
├── tests/                  # pytest 测试
├── AGENTS.md               # 编码代理约定
├── GLOSSARY.md             # 领域术语表
├── pyproject.toml          # 项目配置
├── uv.lock                 # 依赖锁定文件
├── .python-version         # Python 版本指定
├── .gitignore              # Git 忽略配置
├── LICENSE                 # Apache 2.0 许可证
└── README.md               # 项目文档
```

## 安装部署

### 环境要求

- Python 3.10 或更高版本
- uv 包管理工具

### 本地安装

```bash
# 克隆项目
git clone https://github.com/free5394/cf-yx-dns.git
cd cf-yx-dns

# 安装依赖
uv sync --locked --dev

# 复制环境变量模板
cp .env.example .env
```

### GitHub Actions 部署

1. Fork 本项目
2. 在 GitHub 仓库设置中添加以下 Secrets：

| Secret 名称      | 说明                             | 必需 |
| ---------------- | -------------------------------- | ---- |
| `CF_API_TOKEN`   | Cloudflare API Token             | 是   |
| `CF_ZONE_ID`     | Cloudflare 区域 ID               | 是   |
| `CF_DNS_NAME`    | DNS 记录名称（如 `example.com`） | 是   |
| `CF_YX_URL`      | 优选 IP 获取地址                 | 否   |
| `PUSHPLUS_TOKEN` | PushPlus 推送令牌                | 否   |

## 配置说明

### 环境变量

在项目根目录创建 `.env` 文件：

```env
# Cloudflare API 配置
CF_API_TOKEN=your_cloudflare_api_token
CF_ZONE_ID=your_zone_id
CF_DNS_NAME=your_dns_record_name

# 优选 IP 获取地址（可选，默认使用 https://ip.164746.xyz/ipTop.html）
CF_YX_URL=https://ip.164746.xyz/ipTop.html

# PushPlus 推送配置（可选）
PUSHPLUS_TOKEN=your_pushplus_token
```

### Cloudflare API Token 获取

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. 进入 **My Profile** → **API Tokens**
3. 点击 **Create Token** → **Create Custom Token**
4. 配置以下权限：
    - `Zone` → `DNS` → `Edit`
5. 创建成功后复制 Token

### Zone ID 获取

1. 登录 Cloudflare Dashboard
2. 选择目标域名（Zone）
3. 在 **Overview** 页面底部找到 **Zone ID**

### PushPlus 推送配置

1. 访问 [PushPlus](https://www.pushplus.plus/)
2. 登录后获取 Token
3. 将 Token 配置到环境变量

## 使用指南

### 本地运行

```bash
uv run src/dnscf.py
```

### 演练模式

```bash
# 仅显示将要新增/删除的 DNS 记录，不实际修改
uv run src/dnscf.py --dry-run
```

### 检查模式

```bash
# 存在差量时以 1 退出，无差量以 0 退出；同样不修改 DNS
uv run src/dnscf.py --check
```

### GitHub Actions 自动执行

部署完成后，DNS 更新任务将自动按以下规则执行：

| 任务          | 执行周期  | 说明                      |
| ------------- | --------- | ------------------------- |
| dns_cf_push   | 每 6 小时 | 自动更新 DNS 记录         |
| Upstream Sync | 每天      | Fork 仓库自动同步上游更新 |

也可手动触发：在 GitHub 仓库页面点击 **Actions** → 选择 **dns_cf_push** → **Run workflow**

## 工作流程

```text
┌─────────────────┐
│  获取优选 IP     │
│  (第三方服务)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  解析 IP 地址   │
│  (IPv4/IPv6)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  获取现有 DNS   │
│  记录并计算差量  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  先新增缺失记录  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  再删除多余记录  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  发送 PushPlus  │
│    推送通知      │
└─────────────────┘
```

## 常见问题

### Q: 程序执行失败如何排查？

1. 检查 GitHub Secrets 配置是否正确
2. 查看 Actions 日志确认错误信息
3. 本地运行 `uv run src/dnscf.py` 进行调试

### Q: 如何修改优选 IP 来源？

设置 `CF_YX_URL` 环境变量为其他优选 IP 采集地址。

### Q: PushPlus 推送失败？

确认 `PUSHPLUS_TOKEN` 配置正确，且 PushPlus 服务可用。推送失败不影响 DNS 更新功能。

### Q: DNS 记录未更新？

检查 Cloudflare API Token 权限是否包含 DNS 编辑权限。

## 贡献指南

欢迎提交 Issue 和 Pull Request。

### 开发环境

```bash
# 安装开发依赖
uv sync --locked --dev

# 代码检查
uv run ruff check .

# 运行测试
uv run pytest
```

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源许可。

## 致谢

- [Cloudflare](https://www.cloudflare.com/) - DNS 服务提供商
- [PushPlus](https://www.pushplus.plus/) - 消息推送服务

## Star History

![Star History Chart](https://api.star-history.com/svg?repos=free5394/cf-yx-dns&type=Timeline)
