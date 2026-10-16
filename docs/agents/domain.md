# 领域文档（Domain Docs）

工程的这些技能在探查代码库时，应当如何消费本仓库的领域文档。

## 布局结论：single-context

本仓库是 **single-context**：一个仓库共用一份根目录的词条文件，架构决策统一放 `docs/adr/`；不使用 `GLOSSARY-MAP.md`，也不需要各子项目各一份词条文件。这是初始化时与用户确认的结论；在改口之前，所有技能都按它行事。

词条文件名用技能约定的 **`GLOSSARY.md`**（技能就按这个名字读它）。插件面板与提示词里把根目录这份文件称作 `CONTEXT.md`，指的是同一份东西 —— 落到文件系统时写 `GLOSSARY.md`。

## 探查之前先读这些

- 仓库根目录的 **`GLOSSARY.md`**；
- 根目录的 **`GLOSSARY-MAP.md`**（如果存在）：它指向每个上下文各一份的 `GLOSSARY.md`，读其中与你主题相关的每一份；
- **`docs/adr/`**：读与你要动的那块相关的 ADR。

这些文件不存在就**安静跳过**。不要指出它们缺失，也不要提前建议创建：`/domain-modeling` 技能会在术语或决策真正定下来时惰性创建它们。

## 文件结构

单上下文本仓库：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文布局（根目录有 `GLOSSARY-MAP.md` 时）本仓库不适用：那种布局是根目录一份 `GLOSSARY-MAP.md`、系统级决策放 `docs/adr/`，各上下文另有 `src/<context>/GLOSSARY.md` 与 `src/<context>/docs/adr/`。

## 用词表里的词汇

当你的产出提到某个领域概念时（issue 标题、重构提案、假设、测试名），用 `GLOSSARY.md` 里定义的那个词。不要漂到词表明确回避的同义词上。

如果你需要的概念还不在词表里，这是一个信号：要么你在发明这个项目不用的语言（重新考虑），要么是真的有缺口（记下来，交给 `/domain-modeling`）。

## ADR 冲突要摆出来

如果你的产出与已有 ADR 矛盾，明确说出来，不要悄悄覆盖：

> _与 ADR-0007（event-sourced orders）冲突，但值得重开，因为……_
