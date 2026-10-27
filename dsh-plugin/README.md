# dsh-astrbot-memory-bridge（DSH 侧插件）

把 `sync-state.db` 里来自 QQ 陪伴的记忆，以**只读**方式接进 DSH：

- **首轮注入**：最近 N 条记忆摘要挂到 system prompt 的上下文段（带类型与时间标注）
- **三个工具**：`astrbot_memory_search` / `astrbot_memory_recent` / `astrbot_sync_status`
- **静默降级**：同步库不存在、表缺失都不会打断对话

> 完整说明、架构图与安装步骤见仓库根目录的 [README](../README.md) 与 [INSTALL](../INSTALL.md)。

## 安装（概要）

1. 复制本目录到 `<DSH_HOME>/plugins/dsh-astrbot-memory-bridge`
2. 在 DSH profile 的 `package.json` 里加本地依赖：
   `"dsh-astrbot-memory-bridge": "link:<DSH_HOME>/plugins/dsh-astrbot-memory-bridge"`
3. 让 profile 的 `node_modules` 能解析它（跑一次依赖安装，或建 junction）
4. 重启 DSH，插件页确认「运行中」，工具列表应出现上面三个工具

`package.json` 里的 `dsh.bundle.patch` 指向 `cordis.patch.yml`，bundle 机制会把它插进条目列表。

## 配置

通过 `apply(ctx, config)` 传入（或 profile 插件配置 / 环境变量）：

| 键 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| `dbPath` | `DSH_ASTRBOT_SYNC_DB` | `~/.dsh-bridge/sync-state.db` | 同步库路径（只读） |
| `injectLimit` | — | `6` | 首轮注入条数（0–20；0 = 关闭注入） |
| `sourceLabel` | — | `来源=QQ 陪伴` | 注入段落里的来源标注 |
| `timeZone` | — | 系统时区 | 时间戳展示时区 |

## 依赖的 DSH 扩展点

- `systemPrompt.variable(name, () => string)` —— 每轮组 prompt 时求值的动态文本
- `systemPrompt.context({ name, order, text })` —— `text` 必须是字符串，用 `{{name}}` 引用变量
- `tools.register({ name, description, parameters, output: { schema, render }, execute })`
  —— `execute` 返回值、`render` 负责转成内容块

细节与踩坑记录见 [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) 第 4 节。

## 测试

```bash
node tests/test-plugin.mjs
# PLUGIN TEST OK
```

测试会自建一个合成 `sync-state.db`（纯假数据），验证：注入段落形状、`context` 只放变量引用、
三个工具都带 `output` 契约、检索命中/空结果、`kind` 过滤、tombstone 不进注入。

## 只读承诺

插件对同步库只做 `SELECT`，打开方式是 `new DatabaseSync(path, { readOnly: true })`；
不写同步库，也不写任何其他记忆库（meow-memory / memory.db）。