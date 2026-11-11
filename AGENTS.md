# AGENTS.md

本仓库给编码代理（AI agent）的约定。cf-yx-dns 做的事：从第三方接口拉取 Cloudflare 优选 IP，差量同步进 Cloudflare DNS 记录，并把执行结果推给 PushPlus。

## 仓库导航

- 装开发依赖、跑检查、跑测试：见 `README.md` 的「开发环境」（`uv sync --locked --dev`、`uv run ruff check .`、`uv run pytest`），提交前钩子见同节。
- 模块职责与分层（哪一层碰 SDK）：见 `src/cf_yx_dns/__init__.py` 的「模块划分」。
- 编码与评审标准（判断项，机械检查之外）：见 `CODING_STANDARDS.md`。
- 历史质量评估：见 `docs/code-quality-assessment.md`（对提交 `9de674f` 的快照，正文结论不回改；后续处理见其「问题修复进展」表与文末「交付说明」各节）。
- 复审报告归档与统计：报告放 `reviews/`，约定见 `docs/agents/code-review.md`，统计入口 `tools/review_stats.py`。

## Agent skills

### Issue tracker

议题与规格放在本仓库的 GitHub Issues 里（用 `gh` CLI 操作）。See `docs/agents/issue-tracker.md`.

### Triage labels

分类沿用默认五角色标签，标签字符串与角色同名：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

single-context：仓库根目录一份 `GLOSSARY.md`，架构决策放 `docs/adr/`。See `docs/agents/domain.md`.

### 复审归档

每轮复审的结论落盘到 `reviews/YYYYMMDD-<对象提交>.md`，由 Lead 落盘；统计入口 `tools/review_stats.py`。See `docs/agents/code-review.md`.
