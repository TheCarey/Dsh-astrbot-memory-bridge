# 陪伴插件存储与字段速查（互操作性参考）

> 本文是**事实性字段参考**，不是第三方源码副本，也不是官方文档。
> 来源：对 `astrbot_plugin_private_companion` **7.0.2 / 7.x** 的公开行为与数据库结构的只读观察。
> 上游随时可能改字段；若与你的版本不符，以你机器上的 `PRAGMA table_info(<table>)` 为准。
> 本项目与该项目作者无任何关联。

## 1. 数据位置

| 项 | 值 |
| --- | --- |
| 插件名 | `astrbot_plugin_private_companion` |
| 数据根 | `<ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion`（`storage.path` 为空时的默认位置） |
| 主库文件 | `<数据根>/runtime/companion-v2.sqlite3` |
| 分档案 | 源码里常见 `<数据根>/profiles/<profile_id>/…` 布局，导出器用递归查找定位主库 |
| 检索库 | 与主库同目录，可由主库重建，**不是事实源**，同步端不要依赖 |

只读打开方式（与插件自带的 `readonly_uri()` 同一思路）：

```python
sqlite3.connect(pathlib.Path(db).resolve().as_uri() + "?mode=ro", uri=True)
```

## 2. 与桥接相关的表

权威定义在插件自身的 `storage/schema.py` / `memory_schema.py`（本文不复述 SQL）。

### 记忆（`memory_*`）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `memory_atoms` | `atom_id`, `owner_key`, `namespace`, `subject`, `fact_key`, `revision`, `state`, `record_json`, `source_json`, `updated_at` | 记忆原子（一条事实）。正文在 JSON 列里 |
| `memory_revisions` | `atom_id`, `revision`, `state`, `record_json` | 修订历史 |
| `memory_operations` | `operation_id`, `state`, `result_json`, `created_at` | 提议/审批流水（**写回必须走这里**） |
| `memory_changes` | `sequence` (AUTOINCREMENT), `atom_id`, `revision`, `kind`, `occurred_at` | 最稳的增量水位 |
| `memory_guards` / `memory_views` / `memory_reflections` / `memory_sessions` | — | 去重护栏、聚合视图、反思批次、会话 |

### 共同经历 / 关系（`relationship_*`）

| 表 | 说明 |
| --- | --- |
| `relationship_memories` | 正文在 `record_json` 类列 |
| `relationship_memory_revisions` / `relationship_memory_operations` | 修订与流水 |

### 角色生活

| 表 | 说明 |
| --- | --- |
| `role_journals` / `role_journal_revisions` / `role_journal_derivatives` | 日记、修订、衍生摘要 |
| `role_activities` / `role_activity_revisions` | 生活事件/活动 |
| `role_growth_items` / `role_growth_revisions` | 成长值 |
| `role_bindings` / `identity_bindings` / `persona_binding_events` | 角色、人格、身份绑定 |

### 其它（本项目不导出）

`agenda_*`（日程提醒）、`proactive_contacts`（主动联系）、`conversation_*`（会话与投递）、
`configuration_*`、`capability_*`、`model_*`、`screen_*`、`wardrobe_daily_outfits`、`assets`、
`schema_migrations`（版本表）。

## 3. 正文提取的常见形状

`record_json` / `document_json` 里通常是这样的结构（字段名不完全一致，导出器按优先级探测）：

```json
{"content": "一句话事实", "tags": ["…"], "occurred_at": "2026-01-10T09:00:00+08:00"}
```

```json
{
  "title": "某次共同经历",
  "statements": [{"text": "第一句"}, {"text": "第二句"}],
  "occurred_at": "2026-01-10T09:00:00+08:00"
}
```

`memory_views` 的记录以 `document_json.statements[].text` 拼成正文，`title` 作前缀。

## 4. 删除与状态

- 软删除：`state` 变为 `deleted` / `removed` / `archived` / `revoked` 之一；修订历史仍保留。
- 没有物理删除，所以**不要**用 `COUNT(*)` 判断「还剩多少有效记忆」，必须带 `state='active'` 之类条件。
- 同步端把删除写成 `deleted=1`（tombstone），保留行用于审计，但不再注入。

## 5. 对桥接的三条硬约束

1. **只读**：不要写这个库。插件有自己的治理层（`memory_operations` + 护栏），绕过它直写会被
   下一轮治理覆盖或判为异常。
2. **增量优先用 `memory_changes.sequence`**：比 `updated_at` 稳（同一秒多次修改不会漏）。
3. **先看 `schema_migrations`**：插件升级先确认版本，再决定字段探测顺序。