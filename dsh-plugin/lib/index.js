/**
 * dsh-astrbot-memory-bridge —— 宿主半（DSH 侧）。
 *
 * 数据来源：sync-state.db（由 bridge/export_companion.py + bridge/sync.py 从
 * AstrBot 陪伴插件只读导出并增量落库）。本插件只读这个库，不写任何记忆库。
 *
 * 提供：
 *   1) 注入：把「来自 QQ 陪伴的近期记忆」摘要挂进 system prompt 上下文段
 *      （用 systemPrompt.variable 动态求值 + systemPrompt.context 插 {{…}} 变量，
 *       这样每次组装 prompt 都重新查库，不必重启 DSH）；
 *   2) 工具：astrbot_memory_search / astrbot_memory_recent / astrbot_sync_status；
 *   3) 全部只读打开，库不存在时静默降级（不打断会话）。
 *
 * v0.1.0 修复记录（旧版两个坑，都会被 try/catch 静默吞掉，表现为「装了但没有任何效果」）：
 *   - ctx.systemPrompt.context() 必须给 name + 有限 order，且 text 必须是字符串（不是函数），
 *     所以动态文本要走 ctx.systemPrompt.variable(name, () => string) + {{name}} 引用；
 *   - ctx.tools.register() 必须给 output:{schema, render}，execute 返回「值」而不是 content 块。
 *
 * 双向同步（DSH → AstrBot）预留：见文件末尾 TODO 段落。
 */
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

export const name = 'astrbot-memory-bridge'

/** 依赖 systemPrompt（注入）与 tools（注册工具）；两者缺失时插件不挂载。 */
export const inject = ['systemPrompt', 'tools']

/** 默认状态库：~/.dsh-bridge/sync-state.db（与 bridge/sync.py 的 --state 默认值一致）。 */
const DEFAULT_DB = join(homedir(), '.dsh-bridge', 'sync-state.db')
const DEFAULT_INJECT_LIMIT = 6
const MAX_INJECT_LIMIT = 20
const INJECT_CHARS_PER_ITEM = 120
const DEFAULT_SOURCE_LABEL = '来源=QQ 陪伴'
const PROMPT_VARIABLE = 'astrbot_companion'
const CONTEXT_NAME = 'astrbot-memory-bridge:companion'
const CONTEXT_ORDER = 205
const KIND_LABELS = {
  memory: '记忆',
  experience: '共同经历',
  diary: '日记',
  life_event: '生活',
  relationship: '关系',
}

/** 小工具：只读打开、查完即关，避免长连接被同步器锁住。 */
function query(dbPath, sql, params = []) {
  if (!existsSync(dbPath)) return []
  let db
  try {
    db = new DatabaseSync(dbPath, { readOnly: true })
    return db.prepare(sql).all(...params)
  } catch {
    return []
  } finally {
    try {
      db?.close()
    } catch {
      /* ignore */
    }
  }
}

/**
 * 时间标签：库里是带时区的 ISO（多为 +00:00），统一换算成本地时区显示。
 * 认不出时区时退化为原字符串截断。
 */
function fmtTime(value, timeZone) {
  if (!value) return '未知时间'
  const text = String(value)
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(text)
  if (hasZone) {
    const ts = Date.parse(text)
    if (Number.isFinite(ts)) {
      try {
        const parts = new Intl.DateTimeFormat('zh-CN', {
          timeZone,
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }).formatToParts(new Date(ts))
        const pick = (type) => parts.find((p) => p.type === type)?.value ?? ''
        return `${pick('year')}-${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')}`
      } catch {
        /* 未知 timeZone，落到下面的削弱分支 */
      }
    }
  }
  return text.replace('T', ' ').replace(/(\.\d+)?(\+08:00|Z)$/, '').slice(0, 16)
}

function clip(text, max) {
  const s = String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim()
  return s.length > max ? `${s.slice(0, max)}…` : s
}

function label(row, timeZone) {
  const kind = KIND_LABELS[row.kind] || row.kind
  return `${kind}｜${fmtTime(row.updated_at || row.occurred_at, timeZone)}`
}

function injectionText(opts) {
  const { dbPath, limit, sourceLabel, timeZone } = opts
  const rows = query(
    dbPath,
    `SELECT id, kind, content, occurred_at, updated_at, role
       FROM memories
      WHERE deleted = 0
      ORDER BY COALESCE(updated_at, occurred_at) DESC
      LIMIT ?`,
    [limit],
  )
  if (rows.length === 0) return ''
  const lines = rows.map(
    (r, i) => `${i + 1}. [${label(r, timeZone)}] ${clip(r.content, INJECT_CHARS_PER_ITEM)}`,
  )
  return [
    '【来自 QQ 陪伴的近期记忆】',
    sourceLabel,
    ...lines,
    '（这些是和用户在 QQ 上聊过/一起经历过的事；需要更早或更细的内容时用 astrbot_memory_search 检索。）',
  ].join('\n')
}

/** 工具输出契约：DSH 的 tools.register 要求 output { schema, render }。 */
const LINES_OUTPUT = (heading) => ({
  schema: {
    type: 'object',
    additionalProperties: false,
    required: ['heading', 'count', 'lines'],
    properties: {
      heading: { type: 'string' },
      count: { type: 'integer' },
      lines: { type: 'array', items: { type: 'string' } },
    },
  },
  render: (_args, value) => [
    { type: 'text', text: [value.heading, ...value.lines].filter(Boolean).join('\n') },
  ],
  heading,
})

function str(value, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

export function apply(ctx, config = {}) {
  const cfg = config ?? {}
  const dbPath =
    str(cfg.dbPath) || str(process.env.DSH_ASTRBOT_SYNC_DB) || DEFAULT_DB
  const rawLimit = Number(cfg.injectLimit)
  const injectLimit = Number.isFinite(rawLimit)
    ? Math.min(Math.max(Math.trunc(rawLimit), 0), MAX_INJECT_LIMIT)
    : DEFAULT_INJECT_LIMIT
  const sourceLabel = str(cfg.sourceLabel, DEFAULT_SOURCE_LABEL)
  const timeZone = str(cfg.timeZone) || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const logger = ctx.logger
  const opts = { dbPath, limit: injectLimit, sourceLabel, timeZone }

  // 1) 系统提示注入：variable 每次组 prompt 求值，context 只放 {{变量}} 引用
  try {
    if (typeof ctx.systemPrompt?.variable === 'function' && typeof ctx.systemPrompt?.context === 'function') {
      ctx.systemPrompt.variable(PROMPT_VARIABLE, () => injectionText(opts))
      ctx.systemPrompt.context({
        name: CONTEXT_NAME,
        order: CONTEXT_ORDER,
        text: `{{${PROMPT_VARIABLE}}}`,
      })
      logger?.info?.(`astrbot-memory-bridge: prompt context 已挂载（db=${dbPath}）`)
    } else {
      logger?.warn?.('astrbot-memory-bridge: systemPrompt 缺少 variable/context API，注入未挂载')
    }
  } catch (error) {
    logger?.warn?.(
      `astrbot-memory-bridge: 注入挂载失败：${error instanceof Error ? error.message : String(error)}`,
    )
  }

  // 2) 工具
  const tools = [
    {
      name: 'astrbot_memory_search',
      description:
        '在「来自 QQ 陪伴的记忆」里做关键词检索（记忆/共同经历/日记/生活事件）。用于回答“你还记得…”“我们之前聊过…”这类问题。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        required: ['query'],
        properties: {
          query: { type: 'string', description: '关键词或短句，支持空格分隔多个词（任一命中）' },
          limit: { type: 'number', description: '返回条数，默认 8，最多 30' },
        },
      },
      output: LINES_OUTPUT(sourceLabel),
      async execute(args) {
        const rawQuery = String(args?.query ?? '')
        const terms = rawQuery.split(/\s+/).filter(Boolean).slice(0, 6)
        if (terms.length === 0) return { heading: sourceLabel, count: 0, lines: ['请给出检索关键词。'] }
        const limit = Math.min(Math.max(Number(args?.limit) || 8, 1), 30)
        const where = terms.map(() => 'content LIKE ?').join(' OR ')
        const rows = query(
          dbPath,
          `SELECT id, kind, content, occurred_at, updated_at, role, peer
             FROM memories
            WHERE deleted = 0 AND (${where})
            ORDER BY COALESCE(updated_at, occurred_at) DESC
            LIMIT ?`,
          [...terms.map((t) => `%${t}%`), limit],
        )
        if (rows.length === 0) {
          return { heading: sourceLabel, count: 0, lines: [`没有找到与「${rawQuery}」相关的陪伴记忆。`] }
        }
        return {
          heading: sourceLabel,
          count: rows.length,
          lines: rows.map(
            (r) =>
              `- [${label(r, timeZone)}｜${r.peer || '用户'}] ${clip(r.content, 200)}（id=${String(r.id).slice(0, 10)}）`,
          ),
        }
      },
    },
    {
      name: 'astrbot_memory_recent',
      description: '列出最近 N 条来自 QQ 陪伴的记忆（默认 10 条），按最后修改时间倒序。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          limit: { type: 'number', description: '返回条数，默认 10，最多 50' },
          kind: {
            type: 'string',
            description: '可选：只取某一类（memory/experience/diary/life_event/relationship）',
          },
        },
      },
      output: LINES_OUTPUT(sourceLabel),
      async execute(args) {
        const limit = Math.min(Math.max(Number(args?.limit) || 10, 1), 50)
        const kind = str(args?.kind) || null
        const rows = query(
          dbPath,
          `SELECT id, kind, content, occurred_at, updated_at, peer
             FROM memories
            WHERE deleted = 0 ${kind ? 'AND kind = ?' : ''}
            ORDER BY COALESCE(updated_at, occurred_at) DESC
            LIMIT ?`,
          kind ? [kind, limit] : [limit],
        )
        if (rows.length === 0) {
          return { heading: sourceLabel, count: 0, lines: ['同步库里还没有来自 QQ 陪伴的记忆。'] }
        }
        return {
          heading: `${sourceLabel}（最近 ${rows.length} 条）`,
          count: rows.length,
          lines: rows.map((r) => `- [${label(r, timeZone)}] ${clip(r.content, 160)}`),
        }
      },
    },
    {
      name: 'astrbot_sync_status',
      description:
        '查看 QQ 陪伴记忆的同步状态：上次同步时间、活跃条数、已删除条数、最近一次同步统计与错误数。',
      parameters: { type: 'object', additionalProperties: false, properties: {} },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          required: ['dbPath', 'lines'],
          properties: { dbPath: { type: 'string' }, lines: { type: 'array', items: { type: 'string' } } },
        },
        render: (_args, value) => [{ type: 'text', text: value.lines.join('\n') }],
      },
      async execute() {
        const meta = query(dbPath, 'SELECT key, value FROM meta')
        const log = query(
          dbPath,
          'SELECT seq, at, inserted, updated, deleted, skipped, errors FROM sync_log ORDER BY seq DESC LIMIT 1',
        )
        const counts = query(
          dbPath,
          'SELECT SUM(deleted = 0) AS active, SUM(deleted = 1) AS tombstones, COUNT(*) AS total FROM memories',
        )
        const c = counts[0] || {}
        const m = Object.fromEntries(meta.map((r) => [r.key, r.value]))
        const last = log[0]
        const lines = [
          `同步库：${dbPath}`,
          `上次同步：${m.last_sync || '从未'}`,
          `活跃记忆：${c.active || 0} 条｜已删除(tombstone)：${c.tombstones || 0} 条｜总计：${c.total || 0} 条`,
          last
            ? `最近一次：inserted=${last.inserted} updated=${last.updated} deleted=${last.deleted} skipped=${last.skipped} errors=${last.errors}`
            : '最近一次：无记录',
        ]
        return { dbPath, lines }
      },
    },
  ]

  for (const tool of tools) {
    try {
      ctx.tools.register(tool)
    } catch (error) {
      logger?.warn?.(
        `astrbot-memory-bridge: 工具 ${tool.name} 注册失败：${error instanceof Error ? error.message : String(error)}`,
      )
    }
  }
  logger?.info?.(`astrbot-memory-bridge: ${tools.length} 个工具已注册`)
}

// ---------------------------------------------------------------------------
// TODO（预留双向 DSH → AstrBot，本阶段不实现）：
//   1) 在 sync-state.db 增加 outbox 表，记录 DSH 侧希望写回的事件；
//   2) AstrBot 侧 exporter 增加 POST /import 端点（带 token），由 sync.py 推送；
//   3) 写回前必须走陪伴插件自带的记忆治理（memory_operations），不能绕过它的去重与护栏。
// ---------------------------------------------------------------------------