# F1 Cloudflare API 是否拒绝「A 记录存 IPv6」错配（文档证据，2026-11-08）

- 来源票：wayfinder 地图 #3 的子票 #6「F1：真实 Cloudflare API 是否拒绝「A 记录存 IPv6」这类错配」
- 目的：回答「真实 Cloudflare API 对类型与内容地址族错配，是报错拒绝，还是照收？」
- 结论先行：**契约层面不允许错配 —— 官方文档与官方 OpenAPI schema 都把 A 记录的 content 限定为 IPv4、AAAA 限定为 IPv6；但没有任何官方条款写明运行时错误码，故按票面口径标注「文档证据、未实测」。**

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

## 实测路径（未执行，需门禁放行）

- 门禁：写生产，需要人工授权 + 一个专用测试记录名（例如 `f1-probe` 下的子域名），建完立刻删除。
- 请求：

```http
POST https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records
Authorization: Bearer $CF_API_TOKEN
Content-Type: application/json

{"type":"A","name":"<专用测试记录名>","content":"2001:db8::1","ttl":60,"proxied":false}
```

- 预期：HTTP 400、`success:false`、`errors[]` 非空（错误码未知，官方未公布，**不要预设 9005**）。
- 反向再试 `{"type":"AAAA","content":"192.0.2.1"}`。
- 本次未执行：本机 `.env` 读取被宿主安全网按 `secret.basename.env` 拦下（与 2026-11-06 同一条规则），且写生产需要人工授权。

## 局限

- 文档快照日期 2026-11-08；Cloudflare 后续改契约需重查。
- 只有规范层证据，没有 request / response 实测记录。
- 未覆盖 CNAME 与 A 同名、TXT 内容校验等其它记录类型的约束。
