# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.0] — 2026-10-27

首个公开版本：把 AstrBot 陪伴插件的 QQ 陪伴记忆，以只读、单向、增量方式同步进 DSH。

### 新增

- **AstrBot 侧导出插件** `dsh_bridge_exporter`：在 `127.0.0.1:6197` 暴露
  `GET /health` 与 `GET /export?since=`，令牌鉴权（query `token` 或 `X-Bridge-Token` 头），
  以 `mode=ro` 只读打开陪伴插件数据库。
- **桥接脚本** `bridge/export_companion.py`：把陪伴插件记忆导出成稳定 JSONL
  （`memory_atoms` / `relationship_memories` / `role_journals` / `role_activities` /
  `memory_views[experience]` 五类），并写 `.watermark` 时间戳。
- **桥接脚本** `bridge/sync.py`：增量落库到 `sync-state.db`（`memories` / `sync_log` / `meta`），
  支持 `--input` 文件与 `--url` HTTP 两种来源，`--dry-run` 只统计不落库。
- **DSH 侧插件** `dsh-plugin/`：首轮 system prompt 注入 + 三个工具
  （`astrbot_memory_search` / `astrbot_memory_recent` / `astrbot_sync_status`）。
- 去重与删除传播：稳定 `hash = sha256(kind|id|updated_at)`；源端删除写 tombstone 保留行、不再注入。
- 文档：README / INSTALL / ARCHITECTURE / PRIVACY-AUDIT / 陪伴插件表结构速查；
  `examples/config.example.json` 为占位符配置示例。
- 测试：`dsh-plugin/tests/test-plugin.mjs`（合成库 + 挂载契约）、
  `bridge/fixtures/run_tests.py`（导出→同步四步计数校验）。两者均为**纯合成数据**。

### 修复（相对内部开发版的已知问题）

1. **DSH 注入曾经完全无效**：旧代码调用 `ctx.systemPrompt.context({text})`，但真实 API 要求
   `context({name, order, text})` 且 `text` 必须是**字符串**（不能是函数）。
   改为 `ctx.systemPrompt.variable('astrbot_companion', () => string)` +
   `context({name, order, text: '{{astrbot_companion}}'})`，每次组装 prompt 重新查库。
2. **工具注册后调用即失败**：旧代码 `ctx.tools.register({name, description, parameters, execute})`
   缺少 `output`; 真实契约要求 `output: {schema, render}`，且 `execute` 返回**值**而不是 `[{type:'text'}]` 内容块。
   三个工具均已补上 `schema` 与 `render`。
3. **记忆视图漏采**：陪伴插件 7.x 起经历类记忆落在 `memory_views.document_json.statements[].text`，
   `memory_atoms` 常为空；新增 `memory_views`（`kind=experience`）映射，跳过人物卡/习惯等派生视图。
4. **隐私清洗**：所有本机绝对路径、实例 GUID、QQ 号、昵称、令牌一律替换为环境变量 / CLI 参数 /
   占位符；`fixtures` 换成完全合成的示例数据（`companion(10000)` / `user(10001)`）。

### 已知限制

- 单向 AstrBot → DSH；DSH → AstrBot 只有设计与 TODO。
- 增量水位用 `updated_at`，尚未切到 `memory_changes.sequence`。
- 检索为 `LIKE` 匹配，非语义检索。
- 无内置定时同步（v0.1.0 用外部计划任务触发）。