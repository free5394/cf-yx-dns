# 分类标签（Triage Labels）

技能以五个固定的分类角色说话。本文件把这些角色映射到本仓库议题跟踪器里实际使用的标签字符串。

| mattpocock/skills 里的标签 | 本仓库跟踪器里的标签 | 含义 |
| -------------------------- | -------------------- | ---- |
| `needs-triage`             | `needs-triage`       | 维护者需要评估这个议题 |
| `needs-info`               | `needs-info`         | 等报告人补充信息 |
| `ready-for-agent`          | `ready-for-agent`    | 规格已完整，可交给 AFK agent 实现 |
| `ready-for-human`          | `ready-for-human`    | 需要人来实现 |
| `wontfix`                  | `wontfix`            | 不做处理 |

技能提到某个角色时（例如「apply the AFK-ready triage label」），用本表右列对应的标签字符串。

本仓库保留默认词汇表：左右两列一一对应，没有覆盖值。改右列通常只有一个理由 —— 跟踪器里已经在用别的名字（例如用 `bug:triage` 表示 `needs-triage`）；那时才把右列改成实际名字，让技能套用已有标签，而不是造出重复标签。

## 相关但不算分类角色的标签

- **类别标签**：`bug`、`enhancement`（GitHub 默认给的 `documentation`、`duplicate`、`invalid`、`question`、`good first issue`、`help wanted`、`accessibility` 也一并保留）。
- **wayfinder 标签**：`wayfinder:map`、`wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling`、`wayfinder:task`，供 `/wayfinder` 使用，用法见 `docs/agents/issue-tracker.md` 的「Wayfinding 操作」。
