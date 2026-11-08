# F2 生产 zone 记录审计（只读，2026-11-08）

- 来源票：wayfinder 地图 #3 的子票 #4「F2：生产 zone 里是否已有类型与地址族错配的记录」
- 目的：回答「当前 zone 里是否已有错配记录？几条？要不要清理？」
- 结论先行：**受管记录 10 条，错配 0 条，内容非 IP 0 条，重复组 0 组 —— 当前没有可清理的对象。**

## 方法

只读遍历受管记录名下的 A/AAAA 记录（`GET /zones/{id}/dns_records`），复用仓库自身实现做判定，不另写一套口径：

- 取记录：`src/cf_yx_dns/cf_record_store.py:49` `CloudflareRecordStore.list_records`（按 `MANAGED_TYPES` 分类型查询，SDK 负责翻页）
- 判类型：`src/cf_yx_dns/ips.py:65` `parse_ip` + `src/cf_yx_dns/ips.py:82` `record_type`
- 判错配：记录 `type` ≠ 内容地址族推导出的 `record_type`（例如 A 记录里存 IPv6）
- 顺带统计：内容无法解析为 IP 的条数；`(type, content)` 完全相同的重复组数（给 F3 提供生产侧规模）

脚本随本分支提交：`research/f2_audit.py`（同一份也在被 `.gitignore:235` 忽略的 `.scratch/f2_audit.py`）。

## 执行

宿主安全网按规则 `secret.basename.env` 拦住代理读取仓库根 `.env`，因此由本人在仓库根目录执行：

```
.venv\Scripts\python.exe .scratch\f2_audit.py
```

本机 `uv run` 不可用（Low IL 缓存写入被拒，见地图 Notes F4），故直接用 venv 解释器（3.14.4）。

## 原始输出

```
现有记录: A ys.seismic.ccwu.cc -> 198.41.223.20 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 198.41.209.217 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 198.41.209.170 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 198.41.209.142 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 198.41.208.6 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 172.67.77.27 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 162.159.195.144 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 104.18.178.179 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 104.17.71.153 (proxied: False)
现有记录: A ys.seismic.ccwu.cc -> 104.17.8.159 (proxied: False)
获取记录数: 10

=== 汇总 ===
受管记录总数: 10
错配条数: 0
内容无法解析为 IP 的条数: 0
重复组数: 0
```

## 结论

1. **错配：0 条。** 10 条全是 A 记录、内容全是 IPv4，类型与地址族一致。生产中不存在「A 记录里存 IPv6」这类历史脏数据。
2. **非 IP 内容：0 条。** 没有外部导入留下的非地址内容。
3. **重复：0 组。** 当前没有同名同类型同内容的重复记录。
4. **「要不要清理？」→ 现在不需要。** 没有可清理的对象；这只说明「当下没有脏数据」，不等于「不需要清理能力」（F3 问的是未来重叠运行造成的风险）。

## 对其它票的影响

- **#6（F1：真实 API 是否拒绝错配）**：错配在生产中不存在，F1 的价值从「修数据」降级为「确认本地校验是不是唯一防线」。仍值得回答（决定文档要不要写明「这不是可选校验」），但不紧急。
- **#5（F3：重复记录清不掉，是否做清理能力）**：生产里 0 组重复，F3 的取舍从「清现有脏数据」变成「是否为未来重叠运行做能力」，权重下降但问题本身仍成立（判等键不含记录 id 是代码事实）。

## 局限

- 只覆盖受管记录名（`CF_DNS_NAME`）下的 A/AAAA；同名的 CNAME / TXT / MX 不在管辖范围，未读取（`docs/adr/0001-dns-record-management-scope.md`）。
- 单次快照。本地手动运行与 Actions 定时运行重叠的窗口仍存在（`docs/adr/0003-concurrency-boundary.md` 决策 5 明确未做互斥），重复记录随时可能新增。
- 脚本没有覆盖整个 zone（只查受管名），因此「zone 里其它名字下有无错配」不在本结论内。
