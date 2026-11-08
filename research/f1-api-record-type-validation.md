# F1 Cloudflare API 是否拒绝「A 记录存 IPv6」错配（文档证据，2026-11-08）

- 来源票：wayfinder 地图 #3 的子票 #6「F1：真实 Cloudflare API 是否拒绝「A 记录存 IPv6」这类错配」
- 目的：回答「真实 Cloudflare API 对类型与内容地址族错配，是报错拒绝，还是照收？」
- 结论先行：**契约层面不允许错配 —— 官方文档与官方 OpenAPI schema 都把 A 记录的 content 限定为 IPv4、AAAA 限定为 IPv6；但没有任何官方条款写明运行时错误码，故按票面口径标注「文档证据、未实测」。** 追加的「A 记录重复创建是否被拒」同样没有官方条款，待实测（见文末，已并入同一个探针）。

## 既有笔记为何不适用

同图 #4（F2）的研究笔记 `research/f2-prod-zone-record-audit.md`（分支 `research/f2-prod-zone-audit`，commit `7820877`）回答的是生产 zone 的现状（受管 10 条、错配 0、重复 0 组），没有回答 API 的校验行为，因此按 /research 规则另查一次。

## 方法

只取一手来源，全部在 2026-11-08 抓取：

1. 官方 API 参考（由官方 schema 生成）：Create DNS Record 页
   <https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/>
2. Cloudflare 官方 GitHub org 的 API schema：`cloudflare/api-schemas`
   <https://raw.githubusercontent.com/cloudflare/api-schemas/main/openapi.yaml>
3. 官方 DNS 文档：DNS record types
   <https://developers.cloudflare.com/dns/manage-dns-records/reference/dns-record-types/>
4. 官方错误码目录（反向确认是否存在运行时错误条款）

## 证据

### 1. 官方 DNS 文档：API 创建 A/AAAA 时 content 就是对应地址族（规范陈述）

> When creating A or AAAA records using the API: The content of the records is an IP address (IPv4 for A or IPv6 for AAAA).

URL：<https://developers.cloudflare.com/dns/manage-dns-records/reference/dns-record-types/>

同一页还有一句偏行为的旁证：

> Alternative notations of IPv4 addresses (1.1 for 1.0.0.1, for example) are not supported for A records.

→ 说明 A 的 content 确受格式约束（不只是展示性说明），但没写被拒时返回什么。

### 2. 官方 OpenAPI schema：content 带 `format: ipv4` / `format: ipv6` 约束

`https://raw.githubusercontent.com/cloudflare/api-schemas/main/openapi.yaml`（2026-11-08 抓取，约 20.5 MB）：

```yaml
    dns-records_ARecord:
      type: object
      allOf:
        - $ref: '#/components/schemas/dns-records_dns-record-shared-fields'
        - properties:
            content:
              description: A valid IPv4 address.
              example: 198.51.100.4
              format: ipv4
              type: string
              x-auditable: true
```

AAAA 同构：

```yaml
            content:
              description: A valid IPv6 address.
              example: 2400:cb00:2049::1
              format: ipv6
              type: string
              x-auditable: true
```

### 3. 官方 API 参考页：与 schema 一致

<https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/> 里，`a_record` 的 `content` 属性写作 “A valid IPv4 address.”，约束区显示 `format ipv4`；`aaaa_record` 对应 “A valid IPv6 address.” / `format ipv6`。

### 4. 反查：没有找到运行时错误条款

- 官方错误码目录（`/fundamentals/api/reference/errors/`、`/api/errors/`、`/fundamentals/api/reference/error-codes/`）均 404。
- `dns/llms-full.txt` 全文检索 `9005` / `9006` / `must be a valid IPv4` 零命中；官方 `openapi.yaml` 内也没有 `9005` / `9006`。
- 因此不引用任何未经官方原文确认的错误码。

## 判读

**能证明**：API 契约把「A.content 必须是 IPv4」「AAAA.content 必须是 IPv6」写成硬性格式约束；按契约，错配请求属于非法输入。

**不能证明**：服务端运行时一定返回 4xx、错误码是什么、错误文案是什么。schema 的 `format` 是契约声明，不是运行时日志。

结论按票面口径标注为：**拒绝（文档证据、未实测）**。若将来需要行为证据，见下节。

## 对本仓库的含义

1. **代码不需要改。** 建记录路径从内容推导类型（`src/cf_yx_dns/cf_records.py:67` `_canonical_create_target` → `src/cf_yx_dns/ips.py:82` `record_type`），结构上不可能发出「A 存 IPv6」的请求；错配只可能来自外部导入或历史数据。
2. **本地校验的定位是「契约前置防线」，不是「唯一防线」。** 它省一次网络往返、给出中文可读错误；即使 API 会拒绝，也不应该删掉它去依赖服务端。
3. **差量判等里带 `record.type`（`src/cf_yx_dns/cf_records.py:88-103` `_comparison_key`）依然必要。** 它处理的是「已存在的错配记录怎么被判为待删除」，与 API 是否拒绝写入是两件事。
4. **文档措辞可以收紧但非必须。** `docs/code-quality-assessment.md:70` 的「正常 Cloudflare API 校验可能阻止这种数据，本机无法验证」可以改成「API 契约要求 content 与类型匹配（文档证据，未实测）」；`:113` 与 `:141` 的「未实测」表述仍然准确，不必改。

## 追加问题：A 记录重复创建是否被拒（#5 / #9 带入）

#6 的评论追加了一条同批实验要问的问题：#5（F3）决定不做 `--prune-duplicates`，依据是「Cloudflare 对内容完全相同的重复创建会返回 81057 / 81058，所以重复记录根本存不进去」。该依据来自 certbot #10725 的 **TXT** 场景，A 记录没人验证过；#9（create 撞上「已存在」时怎么判）也依赖这一条。

文档侧反查结果：`dns/llms-full.txt` 全文检索 `81057` / `81058` 零命中，官方错误码页 404，官方 `openapi.yaml` 里也没有这两个码 —— **没有官方条款**。所以这一问只能靠实测，已并入下面的同一个探针。

## 实测路径（未执行，需门禁放行）

- 门禁：写生产，需要人工授权 + 一个专用测试记录名（`f1-probe.` 开头的子域），建完立刻删除。
- 一次运行问两件事，脚本随本分支提交：`research/f1_probe.py`。

```
.venv\Scripts\python.exe research\f1_probe.py --name f1-probe.<zone 内子域> --yes
```

脚本的会话上限：运行前要求该记录名下没有任何 A/AAAA 记录（有就拒绝运行），结束后（含异常）删除该名下本次出现的全部记录；只认 `f1-probe.` 前缀；不带 `--yes` 只打印计划。

两个用例的请求体（脚本实际发出的就是这两个）：

```http
POST https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records

{"type":"A","name":"<专用测试记录名>","content":"2001:db8::1","ttl":60,"proxied":false}
{"type":"A","name":"<专用测试记录名>","content":"198.51.100.10","ttl":60,"proxied":false}   # 连发两次
```

- 错配用例预期：HTTP 400、`success:false`、`errors[]` 非空（错误码未知，官方未公布，**不要预设 9005**）。
- 重复用例预期：第二次返回 81057 / 81058 一类错误；若返回 200，说明 A 记录允许重复，按 ADR 0003「修订（2026-11-08）」段的触发条件要让 #5 重开，#9 的判据也要重写。
- 本次未执行：本机 `.env` 读取被宿主安全网按 `secret.basename.env` 拦下（与 2026-11-06 同一条规则），且写生产需要人工授权。

## 局限

- 文档快照日期 2026-11-08；Cloudflare 后续改契约需重查。
- 只有规范层证据，没有 request / response 实测记录。
- 未覆盖 CNAME 与 A 同名、TXT 内容校验等其它记录类型的约束。
