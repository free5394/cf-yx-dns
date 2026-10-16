# AGENTS.md

本仓库给编码代理（AI agent）的约定。cf-yx-dns 做的事：从第三方接口拉取 Cloudflare 优选 IP，差量同步进 Cloudflare DNS 记录，并把执行结果推给 PushPlus。

## Agent skills

### Issue tracker

议题与规格放在本仓库的 GitHub Issues 里（用 `gh` CLI 操作）。See `docs/agents/issue-tracker.md`.

### Triage labels

分类沿用默认五角色标签，标签字符串与角色同名：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

single-context：仓库根目录一份 `GLOSSARY.md`，架构决策放 `docs/adr/`。See `docs/agents/domain.md`.
