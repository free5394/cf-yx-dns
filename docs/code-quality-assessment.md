# cf-yx-dns 源码质量评审报告

## 评估范围与方法

- **评审对象**：`src/cf_yx_dns/`、`src/dnscf.py`、`tests/`、`tools/check_language.py`、`pyproject.toml`，并按仓库基线阅读 `AGENTS.md`、`CODING_STANDARDS.md`、`GLOSSARY.md`、两份 ADR（Architecture Decision Record，架构决策记录）与 README「开发环境」。
- **commit 范围**：工作区受保护路径相对 `HEAD` 无改动；评审基点为 `9de674f9e40069cab1fe7567ef3699d41d9f26c1`（2026-10-21，`build(ci): 增加 actionlint 工作流检查步骤`）。未跟踪的 `.agent-teams/` 不纳入评审。
- **方法**：逐行阅读全部待评源码与测试；用仓库声明的分层、ADR 与 `CODING_STANDARDS.md` 作为主要尺子；执行 ruff 与 pytest 验证；未修改任何文件。
- **规模假设**：单人/定时任务使用的 CLI 工具，核心风险是 DNS 误删、解析中断与告警失效，而非高并发服务。

## 结论摘要

| 维度 | 评分 | 评分理由 |
| --- | ---: | --- |
| 架构与分层 | 5 | `__init__.py:4-9` 声明的分层被严格遵守；Cloudflare SDK 只在 `cf_record_store.py:19` 出现，同步层只依赖三个业务操作（`cf_records.py:6-9`）。 |
| 正确性 | 4 | 差量、IPv6 规范化、空目标护栏、先增后删与幂等性都有代码和测试；但「新增失败后仍继续删除」可能在特定失败下把记录名清空，与 ADR 0002 的可用性目标冲突。 |
| 错误处理与健壮性 | 4 | 抓取重试（`ips.py:37-47`）、单条失败不中断（`cf_records.py:181-195`）、中文错误提示整体到位；主要缺口是失败后的删除策略与少数过宽 `except Exception`。 |
| 配置与凭据 | 4 | `config.py` 是唯一环境变量读取点，缺配置集中报错（`cli.py:70-78`）；未发现 token 进入日志或推送正文；Zone ID 会输出但不是凭据。 |
| 测试质量 | 4 | 94 个测试覆盖差量、护栏、顺序、失败收集、CLI 退出码与推送响应；还有一条真实 SDK + 模拟 HTTP 传输的跨 seam 测试；缺「新增失败后删除」的关键分支。 |
| 代码风格与可读性 | 5 | 函数短小、命名清楚、注释与提示为简体中文、魔数有命名常量；`CODING_STANDARDS.md` 仅有的「行为不变需对账」标准不适用于本次静态评审，未发现实际违反点。 |
| 工具链与文档一致性 | 4 | README、`.githooks/pre-commit`、CI 与 `pyproject.toml` 基本一致；`.env.example` 默认启用了非默认来源 URL，与 README 的「可选/默认」表述不一致。 |
| 安全 | 4 | 第三方页面内容只经 `ipaddress` 校验后进入 DNS（`ips.py:107-121`），Actions 全部按 SHA 固定；未发现凭据泄漏路径；并发与外部异常数据的边界仍需注意。 |

**总体判断**：这是一个分层清晰、测试认真、文档与实现高度对齐的小型工具。没有 P0。最值得优先处理的是 P1 的「新增失败后仍删除旧记录」策略；其余为运维可观测性、边界一致性和文档小偏差。

## 问题清单

### P0

无。

### P1

#### 1. 新增失败后仍执行删除，极端情况下会把记录名清空

- **证据**：
  - `src/cf_yx_dns/cf_records.py:181-187`：新增逐条捕获失败，只追加 `失败:` 消息并 `continue`。
  - `src/cf_yx_dns/cf_records.py:190-195`：无论新增阶段是否有失败，删除阶段照常执行。
  - `docs/adr/0002-create-before-delete-sync-order.md:21-22`：ADR 明确后果是「同步过程中该记录名始终保有解析」「新增阶段是超集，删除阶段收敛到目标集合」。
  - `tests/test_cf_records.py:183-204`：新增失败与删除失败各自有测试，但没有覆盖「新增失败之后是否仍删除」的组合分支。
- **影响**：假设现有记录只有 `104.16.1.1`，目标是 `104.16.2.2`；新增因 API 权限/限流失败，删除旧记录成功，则该记录名下变成 0 条记录，直接违背 ADR 0002 要避免的解析中断。下次成功执行才会恢复。
- **建议修复方向**：最小修复是「任一新增失败时跳过删除阶段」并以退出码 1 结束，保持旧记录可用；若想更细，可以按记录类型跳过对应删除。无论选哪种，都应同步修订 ADR 0002 与新增回归测试（新增失败 + 待删除旧记录 → 不调用 `delete_record`）。

### P2

#### 2. 正常同步路径中推送失败只写 stdout，任务仍绿色退出

- **证据**：
  - `src/cf_yx_dns/cli.py:124-132`：`notify.push_plus(...)` 的返回值未被接收；退出码只由 DNS 失败消息决定。
  - `src/cf_yx_dns/notify.py:45-64`：网络异常、HTTP 非 200、非法 JSON、业务 code 非 200 全部只 `print(...)` 到 stdout 并返回 `False`。
  - `GLOSSARY.md:65-67` 与 `README.md:220-222`：文档刻意约定「推送失败不影响 DNS 更新功能」，所以这不是文档错误，而是可观测性缺口。
- **影响**：`PUSHPLUS_TOKEN` 失效或 PushPlus 故障时，DNS 同步成功、Actions 任务显示成功、用户又收不到唯一的通知通道，故障可能长期不被发现。
- **建议修复方向**：保持退出码语义不变也可以先做最小改进：推送失败时输出到 stderr，并考虑在 README 说明「任务绿色不代表推送成功」；若运维需要更强保证，可加一个显式 `--strict-notify` 开关，避免违背现有领域约定。

#### 3. 并发执行没有进程级互斥，工作流排队只保护同名 workflow

- **证据**：
  - `.github/workflows/dns_cf.yml:11-14`：`concurrency.group: dns_cf_push`、`cancel-in-progress: false`，只避免同一 workflow 的定时与手动触发重叠。
  - `src/cf_yx_dns/cf_records.py:146-158` 与 `161-195`：计划基于一次 `list_records` 快照，执行期间不再读取外部变化；源码中没有任何锁或条件更新。
- **影响**：本地手动运行与 Actions 定时任务重叠时，两轮都可能基于同一旧快照计算差量，重复创建同一目标 IP；后续运行是否能把重复记录收敛，取决于 Cloudflare 对重复记录的存储/拒绝行为。
- **建议修复方向**：如果并发是真实使用场景，先补一条「外部在计划与执行之间改动记录」的设计说明；再考虑最小互斥（例如本地/CI 共用的锁文件并不现实时，至少文档声明禁止并行）。不要引入分布式锁这类超出工具规模的机制。

### P3

#### 4. 差量比较完全忽略 `record.type`，类型与内容族不匹配的异常记录会被误判为已存在

- **证据**：
  - `src/cf_yx_dns/cf_records.py:87-93`：`_comparison_key(record)` 只看 `record.content`，不看 `record.type`。
  - `src/cf_yx_dns/cf_records.py:108-110`：`existing_keys` 与 `target_keys` 直接按规范化 IP 做集合比较。
  - `tests/test_cf_records.py:105-110`：仓库自己承认「内容不是公网 IP（历史遗留）」是可能输入，但该测试只覆盖非 IP 字符串，不覆盖类型与 IP 族不匹配。
- **影响**：若历史/外部数据存在 A 记录内容为 IPv6（或 AAAA 记录内容为 IPv4），目标 IPv6 会被判为已存在，不创建 AAAA，也不删除这条异常 A 记录，目标状态不达成。正常 Cloudflare API 校验可能阻止这种数据，本机无法验证。
- **建议修复方向**：`_comparison_key` 中结合 `(record.type, canonical_ip)`；对类型与内容族不匹配的记录按待删除处理。先加测试固定预期，再确认是否与 ADR 0001 的「历史遗留内容按原始字符串比较」语义兼容。

#### 5. `.env.example` 复制后默认切换到非默认 IP 来源

- **证据**：
  - `.env.example:9-10`：第 9 行注释写着默认地址，第 10 行却启用了另一个 GitHub raw 地址。
  - `README.md:95`、`README.md:110-111`：`CF_YX_URL` 描述为可选，默认 `https://ip.164746.xyz/ipTop.html`。
  - `src/cf_yx_dns/config.py:31-33`：环境变量非空时优先于默认值。
- **影响**：用户按 README「复制模板」后，实际来源与文档宣称的默认值不同，排障时容易误判。
- **建议修复方向**：将 `.env.example` 第 10 行注释掉，或明确写「示例：如需更换来源再取消注释」。

#### 6. 抓取重试捕获所有 `Exception`，编程错误会被伪装成网络失败

- **证据**：
  - `src/cf_yx_dns/ips.py:37-46`：`httpx.get(...)` 外层是 `except Exception as e`，然后统一打印「获取优选 IP 失败」并重试 5 次。
- **影响**：若未来出现 `TypeError`、配置读取 bug 等确定性异常，会被重试 15 秒后归因失败，错误文本可能不足以定位真正原因。
- **建议修复方向**：至少收窄为 `httpx.HTTPError`/`httpx.TransportError`，让编程错误直接抛出；保留非 200 状态码的重试逻辑不变。

#### 7. `check_language` 只检查 `print`/`raise` 的第一段字符串字面量

- **证据**：
  - `tools/check_language.py:51-62`：只识别 `print(...)` 与 `raise Xxx(...)`，且 `_first_string_literal` 只取第一个字面量。
  - `tools/check_language.py:5-8`：脚本自身明确声明这是机械检查的两类，不做语义判断。
- **影响**：`logging`、`sys.stderr.write`、先拼变量再 `print(value)` 都绕过检查。这是已知范围限制而非隐性缺陷，但它的保护强度低于直觉。
- **建议修复方向**：不必扩大成复杂 lint；在 README/脚本 docstring 里写清「这是最低线，语义仍按 CODING_STANDARDS 人工评审」即可，避免团队误以为全量覆盖。

## 亮点

- **分层声明与实现一致**：`src/cf_yx_dns/__init__.py:4-9` 声明各模块职责；`grep` 全源码显示 Cloudflare SDK 只在 `src/cf_yx_dns/cf_record_store.py:19` 导入，`cf_records.py:15-21` 只导入 `dataclass` 与 `ips`，同步层确实不认识 SDK。
- **组合根清晰**：`src/cf_yx_dns/cli.py:83-88` 只在 CLI 创建一次 `CloudflareRecordStore`，并把它显式传给 `plan_sync` 与 `apply_sync`；`tests/test_cli.py:107-130` 专门断言两个动词复用同一实例。
- **空目标护栏在读取前生效**：`src/cf_yx_dns/cf_records.py:143-147` 先检查空目标再调用 `store.list_records`；`tests/test_cf_records.py:117-122` 断言连读取事件都没有。
- **先增后删顺序被代码和测试同时钉住**：`src/cf_yx_dns/cf_records.py:181-195` 先遍历 `to_create` 再遍历 `to_delete`；`tests/test_cf_records.py:156-171` 断言 `store.events == ["create", "delete"]`。
- **IPv6 规范化避免反复增删**：`src/cf_yx_dns/ips.py:107-121` 统一压缩写法；`src/cf_yx_dns/cf_records.py:87-93` 差量比较使用规范化键；`tests/test_cf_records.py:98-103` 覆盖展开/压缩等价。
- **单条失败语义可诊断**：`src/cf_yx_dns/cf_records.py:181-195` 每条失败带原始异常文本和「失败:」前缀；`src/cf_yx_dns/cli.py:129-132` 按前缀统计并以退出码 1 结束，符合 ADR 0002 第 15-17 行。
- **真实 SDK seam 测试质量高**：`tests/test_cf_records.py:255-311` 使用真实 `Cloudflare` SDK + `httpx.MockTransport`，覆盖 A/AAAA 分页、`name.exact` 请求参数、新建请求体形状（`zone_id` 路径、`ttl`、`proxied`、`comment`）。
- **凭据处理干净**：`CF_API_TOKEN` 只在 `cli.py:85-88` 传入构造器，再由 `cf_record_store.py:43-47` 传入 SDK；全源码 `print` 列表核对（见上文命令输出）没有 token 输出。`PUSHPLUS_TOKEN` 只进入请求 payload（`notify.py:38-46`），不打印。
- **第三方输入校验严格**：候选先由正则提取（`ips.py:155-163`），再经 `ipaddress.ip_address` 与公网/组播/保留地址过滤（`ips.py:83-104`），写入前再次校验（`cf_records.py:66-84`）。
- **Actions 供应链控制好**：`.github/workflows/dns_cf.yml:24-28`、`ci.yml:16-30` 的第三方 action 均按 commit SHA 固定；DNS workflow 限定 `contents: read` 与必要 `actions: write`（`dns_cf.yml:19-21`）。
- **兼容入口合理且极薄**：`src/dnscf.py:8-13` 只转发到 `cf_yx_dns.cli.main`，没有复制业务逻辑；README 的 `uv run src/dnscf.py` 用法与其存在一致（`README.md:143-157`）。

## 未能验证项

- **真实 Cloudflare 行为**：A 记录能否存 IPv6 内容、重复 A/AAAA 记录是否被 API 拒绝、SDK 异常文本是否可能包含 Authorization header——本地无网络且未调用真实 API，无法验证；因此问题 4 保留为有条件触发的 P3，未升级。
- **真实 PushPlus 行为**：仅依据 `tests/test_notify.py:27-107` 的模拟响应验证 code/HTTP/JSON/网络错误分支，未验证线上响应格式变化。
- **进程级并发后果**：工作流内并发已由 `dns_cf.yml:11-14` 排队；本地与 Actions 并发导致的重复记录最终状态无法离线验证。
- **`uv run`**：按用户说明跳过，未重复尝试；改用 `.venv` 下的 ruff 与 pytest，结果见下。
- **`.env` 实际内容**：为避免读取真实凭据，未打开 `.env`；仅阅读 `.env.example`。

## 附：执行过的检查命令与真实结果

```text
git log -1 --format='%H %ad %s' --date=iso
9de674f9e40069cab1fe7567ef3699d41d9f26c1 2026-10-21 16:22:32 +0800 build(ci): 增加 actionlint 工作流检查步骤

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --capture=no
94 passed in 0.41s
```

## 问题修复进展

本报告是 2026-11-05 对提交 `9de674f` 的评估快照，正文结论描述的是当时的代码状态，不回头改写。后续处理在此登记，读正文时请对照本表：

| 编号 | 问题 | 状态 | 依据 |
| --- | --- | --- | --- |
| P1 | 新增阶段失败后仍删除多余记录 | 已修复（`d11c851`） | 任一新增失败即整体跳过删除阶段，并追加「跳过: …」说明；补两条回归测试；ADR 0002 增「修订（2026-11-05）」一节 |
| P2 | 正常同步路径推送失败只写 stdout、任务仍绿色退出 | 已修复（`bbfb33f`） | notify 失败分支改走 stderr；CLI 接住返回值并追加「警告: 同步结果推送失败」；退出码语义按 GLOSSARY「推送通道」保持不变 |
| P2 | `dns_cf.yml` 的 `concurrency` 只保护同名 workflow | 已处理 | 定时与手动触发本就共用同一个 group（静态字符串），互斥已经成立；补 15 分钟 `timeout-minutes` 防止挂起的运行饿死队列，边界与残留风险写入 ADR 0003 与 README。跨 workflow 的作业级互斥未引入——没有可共享的锁介质，见 ADR 0003 决策 5。超时是强制取消而非失败，`if: failure()` 的告警在这种强杀下不保证发出，已在 ADR 0003「后果」中记为残留风险 |
| P3-4 | 差量比较忽略 `record.type` | 已修复 | 核实为真问题但触发面窄：本工具创建时类型由内容推导（`cf_records.py` 的 `_canonical_create_target`），正常链路不会产出错配记录；错配记录按「只可能来自外部导入或历史数据」处理。改为「类型 + 规范化 IP」判等：`_comparison_key` 返回 `(record.type, canonical)`、新增 `_target_key(ip)`，两侧都做规范化，错配记录判为待删除。补两条测试（错配不算已存在；目标展开写法能命中压缩写法的现有记录）；GLOSSARY「规范化 IP」补记录身份定义。**2026-11-08 补实测**（issue #6；生产 zone 的专用测试记录名 `f1-probe.seismic.ccwu.cc`，由独立探针直调 API、未走本工具 `apply_sync` 链路，建完即删、该名下剩余 0 条；探针与执行命令见 #6，脚本在分支 `research/f1-api-record-type-validation`（`a745c6e`），未合主干）：`type=A` + `2001:db8::1` → `HTTP 400`、`errors[0].code=9005`、`Content for A record must be a valid IPv4 address.`；同 name + type + content + proxied 连建两次 → 第二次 `HTTP 400`、`errors[0].code=81058`、`An identical record already exists.`。即本轮测的这两类会被拦；AAAA 侧的错配与「同名不同内容」（81057）本轮未测，不要按本条外推。正文 `:70` 整句、`:113` 的前两项（A 记录能否存 IPv6、重复创建是否被拒）按快照冻结不改，以本行为准；`:113` 第三项（SDK 异常文本是否含 Authorization header）本轮未涉及，仍属未验证。该实测已被 ADR 0002「修订（2026-11-08）」与 #9 的判据消费（`src/cf_yx_dns/cf_record_store.py` 的 `DUPLICATE_RECORD_CODES = frozenset({81058})`），行为变更登记在 ADR 与 #9，本表不另记。本地校验保留为契约前置防线（省一次往返、能出中文报错），不是唯一防线 |
| P3-5 | `.env.example` 复制后默认切到非默认来源 | 已修复 | `.env.example` 里 `CF_YX_URL` 改为注释状态，写明「不设置即默认 `https://ip.164746.xyz/ipTop.html`，下面这行只是示例」。凭据占位行未改动 |
| P3-6 | `ips.py` 抓取重试捕获所有 `Exception` | 已修复 | 收窄为 `except httpx.HTTPError`；来源地址提到循环外只读一次（配置读取失败不再被重试掩盖）；其余异常直接抛出。来源地址格式非法（`httpx.InvalidURL`，它不是 `HTTPError` 的子类）属用户配置错误，单独处理：不重试、打印中文错误后返回 `None`，交给 CLI 沿用既有「获取失败」路径报错退出——既不静默吞掉，也不甩 traceback（堆栈回溯）。补三条测试（编程错误只尝试一次且不退避；地址只读一次；地址非法只报一次）。其余非网络异常仍以 traceback（堆栈回溯）暴露，属刻意 fail-fast（快速失败） |
| P3-7 | `check_language.py` 只查 `print`/`raise` 首个字面量 | 已补文档（不改代码） | 核实为报告自己认定的「已知范围限制」，脚本 docstring 早已声明不做语义判断。README「开发环境」写明它是机械最低线（`logging`、`sys.stderr.write`、先拼字符串再 `print` 都看不到），术语释义与语义仍按 `CODING_STANDARDS.md` 人工评审 |

正文第 2 条（推送失败只写 stdout）的「建议修复方向」里提到的 `--strict-notify` 本轮未采纳：它会打破「推送失败不影响 DNS 同步结果」这条既有约定，需要连带改文档与 CI 语义，属于单独决策。

P3 四条处理后的检查结果（2026-11-06）：`pytest` **104 passed**（上述改动新增 6 条测试）、`ruff check .` 全绿、`tools/check_language.py` 通过（9 个文件）、`actionlint` 退出码 0。

提交后的独立只读复审另外指出两处口径问题，已在同一次收口里修掉：①`_target_key` 只推导类型、不规范化内容，目标传 IPv6 展开写法时会与压缩写法的现有记录错判（现已两侧都规范化，并补一条反向用例）；②把「非网络异常」一律说成「编程错误」不成立——`httpx.InvalidURL` 来自用户可配置的 `CF_YX_URL`，属配置错误，现已单独处理为不重试、中文报错、返回 `None` 交给 CLI。复审还指出 P3-4 一行把「Cloudflare API 是否拦错配」这个未验证项写成了确定事实，已改写为本工具自身推导口径。

## 交付说明（P3 收口，2026-11-06）

本轮对报告四条 P3 逐条核实后落地：三条确认是真问题并修复——差量判等忽略 `record.type`、`.env.example` 复制后默认切到非默认来源、`ips.py` 抓取重试吞掉所有异常；第四条（`check_language` 只查首个字面量）确认为脚本自己声明的范围限制，只补文档不改代码。行为变化共两处，均按 diff 对账并写进对应提交信息：差量判等键从「规范化 IP」改为「记录类型 + 规范化 IP」，且 `compute_diff` 自己负责规范化与去重——正常数据判定不变，只有「A 记录里存 IPv6」这类外部导入的错配记录会从「视为已存在、不删除」变成「创建对应类型的目标记录 + 删除这条异常记录」，`--dry-run` 展示同时回到规范化写法；抓取重试收窄为 `httpx.HTTPError`，来源地址或代理配置非法（`httpx.InvalidURL`，它不是 `HTTPError` 的子类）按配置错误处理——不重试、打印中文错误、返回 `None` 交给 CLI 走既有「获取失败」退出 1 路径，其余异常直接抛出，不再被重试十几秒后伪装成「网络不通」。落地为四个提交：`d562612`（四条主体修复）、`e00cfc5`（第一轮复审返工）、`8a7294a`（第二轮复审返工）、`109006f`（`plan_sync` 入参说明与本轮口径对齐，注释级改动）。验证为 `pytest` 104 passed、`ruff` 全绿、`check_language` 9 个文件通过、`actionlint` 退出码 0，并以真实进程复现了两条路径：非法 `CF_YX_URL` → 退出码 1、中文错误、无 traceback（堆栈回溯）；合法地址 + 非法 `HTTP_PROXY` → 返回 `None` 且文案不误指来源地址。三轮独立只读复审分别给出「1 个 P2」「1 个 P2」「无 P0/P1/P2」，前两轮抓到的问题已全部修完。剩余风险两处，均为已披露的取舍：P3-7 的覆盖面由机械检查降级为文档约束；编程类异常以 traceback（堆栈回溯）形式暴露，属刻意 fail-fast（快速失败）。
