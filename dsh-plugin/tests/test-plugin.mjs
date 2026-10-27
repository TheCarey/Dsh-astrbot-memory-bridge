// 独立逻辑测试：不需要 DSH 运行时，只验证插件的挂载契约与三个工具。
//
//   node tests/test-plugin.mjs
//
// 会在系统临时目录里建一个合成 sync-state.db（纯假数据），测完即删。
import assert from 'node:assert'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

const { apply, inject, name } = await import('../lib/index.js')

// --- 1) 建合成状态库（结构与 bridge/sync.py 的 SCHEMA 一致） -----------------
const dir = mkdtempSync(join(tmpdir(), 'dsh-bridge-test-'))
const DB = join(dir, 'test-sync-state.db')

const db = new DatabaseSync(DB)
db.exec(`
CREATE TABLE memories (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
    occurred_at TEXT, updated_at TEXT, deleted INTEGER NOT NULL DEFAULT 0,
    role TEXT, peer TEXT, source TEXT, version TEXT,
    hash TEXT NOT NULL, first_seen TEXT NOT NULL, last_sync TEXT NOT NULL
);
CREATE TABLE sync_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, source TEXT,
    inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, note TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
`)
const kinds = ['memory', 'experience', 'diary', 'life_event', 'relationship']
const insert = db.prepare(
  'INSERT INTO memories (id,kind,content,occurred_at,updated_at,deleted,role,peer,source,version,hash,first_seen,last_sync) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
)
for (let i = 0; i < 9; i += 1) {
  const stamp = `2026-10-26T0${i}:00:00+08:00`
  insert.run(
    `m00${i}`,
    kinds[i % kinds.length],
    `记忆条目 ${i}：用户和助手在周${i % 7}聊过的第 ${i} 件事`,
    stamp,
    stamp,
    0,
    'companion',
    'user',
    'qq/companion',
    '1',
    `h${i}`,
    stamp,
    stamp,
  )
}
insert.run('s001', 'memory', '唯一的哨兵记忆：只有这条包含「蓝色鲸鱼」', '2026-10-26T09:30:00+08:00', '2026-10-26T09:30:00+08:00', 0, 'companion', 'user', 'qq/companion', '1', 'hs', 'x', 'x')
insert.run('d001', 'memory', '这条已被删除的记忆', '2026-10-26T05:00:00+08:00', '2026-10-26T05:00:00+08:00', 1, 'companion', 'user', 'qq/companion', '1', 'hdel', 'x', 'x')
db.prepare("INSERT INTO meta (key,value) VALUES ('last_sync', '2026-10-26T06:01:00+08:00')").run()
db.prepare('INSERT INTO sync_log (at,source,inserted,updated,deleted,skipped,errors,note) VALUES (?,?,?,?,?,?,?,?)').run('2026-10-26T06:01:00+08:00', 'fixture', 9, 0, 1, 0, 0, 'run')
db.close()

// --- 2) 假 ctx：记录 variable / context / register 的调用 --------------------
const captured = { variable: {}, context: null, tools: {} }
const ctx = {
  logger: { info: () => {}, warn: (m) => console.error('warn:', m) },
  systemPrompt: {
    variable(n, fn) {
      captured.variable[n] = fn
    },
    context(opts) {
      captured.context = opts
    },
  },
  tools: {
    register(tool) {
      captured.tools[tool.name] = tool
    },
  },
}

apply(ctx, { dbPath: DB, injectLimit: 5 })

const textOf = (tool, args, value) => {
  const blocks = tool.output.render(args, value)
  return blocks.map((b) => b.text).join('\n')
}

// --- 3) 断言 ---------------------------------------------------------------
assert.equal(name, 'astrbot-memory-bridge')
assert.deepEqual(inject, ['systemPrompt', 'tools'])

// 注入：context 只放变量引用，动态文本由 variable 求值
assert.ok(captured.context, 'systemPrompt.context 已注册')
assert.equal(captured.context.text, '{{astrbot_companion}}', 'context.text 是字符串变量引用')
assert.ok(Number.isFinite(captured.context.order), 'context 带有限 order')
assert.ok(captured.context.name, 'context 带 name')

const injectFn = captured.variable.astrbot_companion
assert.equal(typeof injectFn, 'function', 'systemPrompt.variable 已注册')
const text = injectFn()
assert.ok(text.includes('来自 QQ 陪伴的近期记忆'), '注入段落标题存在')
assert.ok(text.includes('来源='), '注入段落带来源标注')
assert.equal(text.split('\n').length, 2 + 5 + 1, '注入条数 = injectLimit + 头尾')
assert.ok(!text.includes('这条已被删除的记忆'), 'tombstone 不进注入')

// 工具
assert.deepEqual(Object.keys(captured.tools).sort(), [
  'astrbot_memory_recent',
  'astrbot_memory_search',
  'astrbot_sync_status',
])
for (const tool of Object.values(captured.tools)) {
  assert.ok(tool.output?.schema && typeof tool.output.render === 'function', `${tool.name} 有 output 契约`)
}

const search = captured.tools.astrbot_memory_search
const found = await search.execute({ query: '蓝色鲸鱼', limit: 30 })
assert.equal(found.count, 1, '检索命中 1 条')
assert.ok(textOf(search, {}, found).includes('蓝色鲸鱼'), '检索结果可渲染')

const orQuery = await search.execute({ query: '不存在的词 蓝色鲸鱼' })
assert.equal(orQuery.count, 1, '空格分隔多个词是 OR 语义')

const miss = await search.execute({ query: '根本不存在的关键词' })
assert.equal(miss.count, 0, '空结果 count=0')

const recent = captured.tools.astrbot_memory_recent
const recentValue = await recent.execute({ limit: 3 })
assert.ok(textOf(recent, {}, recentValue).includes('最近 3 条'), 'recent 条数正确')
const byKind = await recent.execute({ limit: 50, kind: 'diary' })
assert.equal(byKind.count, 2, 'kind 过滤生效（9 条里 diary 有 2 条）')

const statusValue = await captured.tools.astrbot_sync_status.execute({})
const statusText = textOf(captured.tools.astrbot_sync_status, {}, statusValue)
assert.ok(/活跃记忆：10 条/.test(statusText), '状态含活跃条数')
assert.ok(statusText.includes('tombstone'), '状态含 tombstone')
assert.ok(statusText.includes('2026-10-26T06:01:00+08:00'), '状态含上次同步时间')

console.log('PLUGIN TEST OK')
console.log('--- 注入示例 ---')
console.log(text.split('\n').slice(0, 4).join('\n'))
console.log('--- 状态 ---')
console.log(statusText)

rmSync(dir, { recursive: true, force: true })