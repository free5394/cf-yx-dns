# 议题跟踪：GitHub

本仓库的议题与规格（spec）都是 GitHub issue，所有操作走 `gh` CLI。

## 约定

- **建议题**：`gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- **读议题**：`gh issue view <number> --comments`，配合 `jq` 过滤评论，并一并取出标签。
- **列议题**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label`、`--state` 过滤。
- **评论**：`gh issue comment <number> --body "..."`
- **打标签 / 撤标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

仓库从 `git remote -v` 推断；在克隆目录里跑 `gh` 时它会自动识别（本仓库：`free5394/cf-yx-dns`）。

## Pull request 是否算需求入口

**PRs as a request surface: no.**（本仓库不把外部 PR 当作需求入口；`/triage` 读这一行开关。日后想把外部 PR 纳入分类队列，把它改成 `yes`。）

开关为 `yes` 时，PR 走与 issue 相同的标签与状态，命令换成 `gh pr` 那一套：

- **读 PR**：`gh pr view <number> --comments`，diff 用 `gh pr diff <number>`。
- **列待分类的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，只留 `authorAssociation` 为 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR`、`NONE` 的（丢掉 `OWNER`/`MEMBER`/`COLLABORATOR`）。
- **评论 / 打标签 / 关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 issue 与 PR 共用一个编号空间，所以裸写的 `#42` 可能是两者之一：先 `gh pr view 42`，失败再回落到 `gh issue view 42`。

## 当某个技能说「publish to the issue tracker」

建一个 GitHub issue。

## 当某个技能说「fetch the relevant ticket」

跑 `gh issue view <number> --comments`。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图（map）** 是一个 issue，**子票**是它的 child issue。

- **地图**：一个打了 `wayfinder:map` 标签的 issue，正文放 Notes / Decisions-so-far / Fog。`gh issue create --label wayfinder:map`。
- **子票**：作为 GitHub sub-issue 挂到地图上（用 `gh api` 打 sub-issues 端点）。仓库没开 sub-issue 时，把子票加进地图正文的 task list，并在子票正文顶部写 `Part of #<map>`。标签用 `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。被认领后，assignee 记为驱动的开发者。
- **阻塞**：用 GitHub 原生 issue dependencies，这是标准且界面可见的表示。加边：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞方的数字**数据库 id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number`，也不是 `node_id`）。GitHub 用 `issue_dependencies_summary.blocked_by` 报告（只算未关闭的阻塞方，这才是活的闸门）。用不了 dependency 时，回落到在子票正文顶部写 `Blocked by: #<n>, #<n>`。所有阻塞方都关闭后，子票才算解锁。
- **前沿查询**：列出地图下所有未关闭的子票（`gh issue list --state open`，范围限定在该地图的 sub-issue / task list 内），丢掉有未关闭阻塞方（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行里有未关闭 issue）或已有 assignee 的；按地图顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`，这是本次会话的第一次写。
- **解决**：`gh issue comment <n> --body "<answer>"`，再 `gh issue close <n>`，最后把上下文指针（要点 + 链接）追加到地图的 Decisions-so-far。
