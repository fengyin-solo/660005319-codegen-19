<template>
  <div class="panel health-panel">
    <h4>
      ❤️ 设备健康度
      <span class="rubric-tag">评分口径 {{ overview?.rubric_version || 'v1.0' }}</span>
    </h4>

    <!-- 整组入队操作区 -->
    <div class="op-row">
      <el-select
        v-model="selected" multiple collapse-tags collapse-tags-tooltip
        placeholder="选择需要重算的设备（可多选）" size="small" class="dev-select"
      >
        <el-option
          v-for="d in overview?.items || []" :key="d.id"
          :label="deviceLabel(d.id)" :value="d.id"
        >
          <span>{{ deviceLabel(d.id) }}</span>
          <span class="opt-online">在线率 {{ d.online_rate }}%</span>
        </el-option>
      </el-select>
      <el-button size="small" type="primary" :loading="submitting"
                 :disabled="!selected.length" @click="submit(false)">
        加入队列重算
      </el-button>
    </div>
    <div class="op-sub">
      <el-checkbox v-model="newRound" size="small" border class="round-check">
        作为新一轮重算（允许与上一轮范围重叠）
      </el-checkbox>
      <span class="select-links">
        <a @click="selectAll">全选</a>
        <a @click="selected = []">清空</a>
      </span>
    </div>

    <!-- 跳过/重复提示：明确指出是哪几条 -->
    <el-alert
      v-if="notice" :key="noticeKey" :title="notice.title" type="warning"
      :closable="true" show-icon class="skip-alert"
    >
      <div class="skip-lines">
        <div v-if="notice.lines.duplicate_in_request.length">
          请求内重复，已自动去重：{{ fmtIds(notice.lines.duplicate_in_request) }}
        </div>
        <div v-if="notice.lines.duplicate_in_queue.length">
          已在队列中，同一台设备不能重复入队，跳过：{{ fmtIds(notice.lines.duplicate_in_queue) }}
        </div>
        <div v-if="notice.lines.overlap_last_round.length">
          与上一轮重算范围重叠，跳过：{{ fmtIds(notice.lines.overlap_last_round) }}
          （如需强制重算请勾选“作为新一轮重算”）
        </div>
        <div v-if="notice.lines.invalid.length">
          设备不存在，跳过：{{ fmtIds(notice.lines.invalid) }}
        </div>
        <div v-if="notice.accepted.length" class="accepted-line">
          已入队 {{ notice.accepted.length }} 台：{{ fmtIds(notice.accepted) }}
        </div>
      </div>
    </el-alert>

    <!-- 队列进度 -->
    <div v-if="totals.total > 0" class="progress-box">
      <div class="progress-head">
        <span class="progress-title">
          重算进行中：{{ totals.completed }} / {{ totals.total }} 台
          <span class="progress-ok">成功 {{ totals.success }}</span>
          <span v-if="totals.failed" class="progress-fail">失败 {{ totals.failed }}</span>
        </span>
        <span v-if="queue?.current_device_id != null" class="current-dev">
          正在计算 #{{ queue.current_device_id }}（{{ deviceLabel(queue.current_device_id) }}）
        </span>
      </div>
      <el-progress
        :percentage="progressPct" :stroke-width="10"
        :status="totals.failed && totals.completed === totals.total ? 'exception' : undefined"
      />
      <div v-for="b in queue?.active || []" :key="b.batch_id" class="batch-line">
        <span class="batch-id">批次 {{ b.batch_id.slice(0, 6) }}</span>
        <span v-for="it in b.items" :key="it.item_id"
              class="batch-dot" :class="it.status"
              :title="`#${it.device_id} ${statusText(it.status)}`">
          {{ it.device_id }}
        </span>
      </div>
    </div>

    <!-- 设备健康列表 -->
    <div v-if="loading" class="empty">健康度加载中…</div>
    <div v-else-if="!overview?.items.length" class="empty">暂无设备数据</div>
    <div class="health-list">
      <div v-for="d in (overview?.items || [])" :key="d.id" class="health-row">
        <div class="row-main">
          <div class="row-head">
            <span class="dev-type">{{ deviceLabel(d.id) }}</span>
            <el-tag size="small" :type="tagType(d.status)">{{ d.status_label }}</el-tag>
            <el-tag v-if="d.source === 'recalc'" size="small" type="primary" effect="plain">
              已重算
            </el-tag>
          </div>
          <div class="row-metrics">
            <span class="score" :style="{ color: HEALTH_COLORS[healthLevel(d.health)] }">
              ● 健康 {{ d.health.toFixed(1) }}
            </span>
            <span class="online">在线率 {{ d.online_rate.toFixed(1) }}%</span>
            <span class="computed">{{ fmtTime(d.computed_at) }}</span>
          </div>
          <div class="drag-row">
            <template v-if="d.main_drags.length">
              <span class="drag-label">主要拖低项：</span>
              <el-tooltip
                v-for="drag in d.main_drags" :key="drag.key"
                :content="`${drag.detail}；子项 ${drag.sub_score} 分，权重 ${(drag.weight * 100).toFixed(0)}%，拖低 ${drag.loss} 分`"
                placement="top"
              >
                <span class="drag-tag">{{ drag.label }} -{{ drag.loss }}分</span>
              </el-tooltip>
            </template>
            <span v-else class="drag-ok">各项均衡，无明显拖低项</span>
          </div>
          <div v-if="failedItem(d.id)" class="error-row">
            <span class="err-text">⚠️ {{ failedItem(d.id)!.error || '接口返回异常' }}（已重试 {{ failedItem(d.id)!.attempts - 1 }} 次）</span>
            <el-button size="small" type="warning" plain
                       :loading="retryingId === d.id"
                       :disabled="isQueued(d.id)"
                       @click="retry(d.id)">
              {{ isQueued(d.id) ? '队列处理中' : '单台重试' }}
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getHealth, getQueue, getBatch, enqueueRecalc, retryDevice, errMessage,
} from '../api/health'
import {
  HEALTH_COLORS, DEVICE_TYPE_LABELS, healthLevel,
  type HealthOverview, type QueueStatus, type QueueBatch,
  type QueueItem, type RecalcSkipped,
} from '../types'

const overview = ref<HealthOverview | null>(null)
const queue = ref<QueueStatus | null>(null)
const batchMap = ref<Record<string, QueueBatch>>({})
const selected = ref<number[]>([])
const newRound = ref(false)
const submitting = ref(false)
const retryingId = ref<number | null>(null)
const loading = ref(true)
const notice = ref<{ title: string; lines: RecalcSkipped; accepted: number[] } | null>(null)
const noticeKey = ref(0)
let timer: number | undefined
const knownActive = new Set<string>()
let lastCompleted = -1

const totals = computed(() => queue.value?.totals
  ?? { total: 0, completed: 0, success: 0, failed: 0, running: 0, pending: 0 })
const progressPct = computed(() =>
  totals.value.total ? Math.round(totals.value.completed / totals.value.total * 100) : 0)

function deviceLabel(id: number) {
  const t = overview.value?.items.find(i => i.id === id)?.type
  return `${DEVICE_TYPE_LABELS[t || ''] || t || '设备'} #${id}`
}

function tagType(s: string) {
  const m: Record<string, any> = { RUNNING: 'success', IDLE: 'warning', FAULT: 'danger' }
  return m[s] || 'info'
}

function statusText(s: string) {
  return { pending: '排队中', running: '计算中', success: '成功', failed: '失败' }[s] || s
}

function fmtIds(ids: number[]) {
  return ids.map(id => `#${id}`).join('、')
}

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString()
}

function selectAll() {
  selected.value = (overview.value?.items || []).map(i => i.id)
}

function isQueued(id: number) {
  return queue.value?.queued_device_ids.includes(id) ?? false
}

// 该设备最近一次批次条目是否失败（供逐条重试）
function failedItem(id: number): QueueItem | null {
  let found: QueueItem | null = null
  for (const b of Object.values(batchMap.value)) {
    for (const it of b.items) {
      if (it.device_id === id && (found === null || it.created_at >= found.created_at)) {
        found = it
      }
    }
  }
  return found && found.status === 'failed' ? found : null
}

async function loadHealth() {
  try {
    overview.value = await getHealth()
  } catch (e) {
    ElMessage.error(`健康度加载失败：${errMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function submit(forceNewRound: boolean) {
  if (!selected.value.length) return
  submitting.value = true
  try {
    const res = await enqueueRecalc(selected.value, forceNewRound || newRound.value)
    const s = res.skipped
    const anySkip = s.duplicate_in_request.length || s.duplicate_in_queue.length
      || s.overlap_last_round.length || s.invalid.length
    if (res.batch_id) {
      if (anySkip) {
        notice.value = { title: res.message, lines: s, accepted: res.accepted }
        noticeKey.value++
      } else {
        ElMessage.success(res.message)
      }
      // 入队成功后保留选择无意义；新一轮勾选只生效一次
      selected.value = []
      newRound.value = false
    } else {
      notice.value = { title: res.message, lines: s, accepted: [] }
      noticeKey.value++
    }
    await poll()
  } catch (e) {
    ElMessage.error(`入队失败：${errMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function retry(id: number) {
  const item = failedItem(id)
  retryingId.value = id
  try {
    const res = await retryDevice(id, item?.item_id)
    ElMessage.success(res.message)
    // 同步更新本地批次条目，避免下一次轮询前仍是失败态
    if (item) {
      item.status = 'success'
      item.result = res.result
      item.error = null
    }
    await loadHealth()
  } catch (e) {
    ElMessage.error(`设备 #${id} 重试失败：${errMessage(e)}`)
    if (item) item.attempts += 1
  } finally {
    retryingId.value = null
  }
}

async function poll() {
  try {
    const q = await getQueue()
    queue.value = q
    for (const b of q.active) {
      batchMap.value[b.batch_id] = b
      knownActive.add(b.batch_id)
    }
    // 上一轮还在 active、这次已不在 -> 批次结束，拉取最终条目（含失败原因）
    for (const id of Array.from(knownActive)) {
      if (!q.active.some(b => b.batch_id === id)) {
        knownActive.delete(id)
        try {
          batchMap.value[id] = await getBatch(id)
        } catch { /* 批次过期可忽略 */ }
      }
    }
    if (q.totals.completed !== lastCompleted) {
      lastCompleted = q.totals.completed
      await loadHealth()
    }
  } catch {
    /* 轮询瞬时失败静默，下个周期再试 */
  }
}

onMounted(async () => {
  await loadHealth()
  poll()
  timer = window.setInterval(poll, 1000)
})
onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.health-panel{padding-bottom:10px}
.panel h4{color:#64b5f6;margin-bottom:8px;font-size:13px;display:flex;align-items:center;gap:8px}
.rubric-tag{font-size:10px;font-weight:400;color:#94a3b8;border:1px solid #334155;border-radius:3px;padding:0 5px}
.op-row{display:flex;gap:6px;align-items:center}
.dev-select{flex:1}
.op-sub{display:flex;justify-content:space-between;align-items:center;margin:6px 0}
.round-check{margin-left:-6px}
:deep(.round-check .el-checkbox__label){font-size:11px;color:#94a3b8;padding-left:6px}
.select-links{display:flex;gap:8px;font-size:11px}
.select-links a{color:#64b5f6;cursor:pointer}
.opt-online{float:right;color:#64748b;font-size:11px}
.skip-alert{margin:6px 0}
.skip-lines{font-size:11px;line-height:1.7}
.accepted-line{color:#86efac}
.progress-box{background:#112233;border:1px solid #1e3a5f;border-radius:4px;padding:8px;margin:6px 0}
.progress-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;gap:8px;flex-wrap:wrap}
.progress-title{font-size:12px;color:#e0e6ed;font-weight:600}
.progress-ok{color:#22c55e;font-weight:400;margin-left:8px}
.progress-fail{color:#f87171;font-weight:400;margin-left:8px}
.current-dev{font-size:11px;color:#fbbf24}
.batch-line{margin-top:6px;display:flex;flex-wrap:wrap;gap:3px;align-items:center;font-size:10px}
.batch-id{color:#64748b;margin-right:4px}
.batch-dot{padding:1px 5px;border-radius:3px;background:#334155;color:#cbd5e1}
.batch-dot.running{background:#1d4ed8;color:#fff}
.batch-dot.success{background:#14532d;color:#86efac}
.batch-dot.failed{background:#7f1d1d;color:#fca5a5}
.health-list{display:flex;flex-direction:column;gap:5px;max-height:300px;overflow-y:auto;margin-top:6px}
.health-row{background:#112233;border-radius:4px;padding:7px 9px;border-left:3px solid #1e3a5f}
.row-head{display:flex;gap:6px;align-items:center}
.dev-type{font-size:12px;color:#e0e6ed;font-weight:600}
.row-metrics{display:flex;gap:10px;align-items:center;margin-top:4px;font-size:11px}
.score{font-weight:700;font-size:13px}
.online{color:#94a3b8}
.computed{color:#475569;margin-left:auto;font-size:10px}
.drag-row{margin-top:5px;font-size:10px;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.drag-label{color:#64748b}
.drag-tag{background:#7f1d1d33;border:1px solid #7f1d1d55;color:#fca5a5;padding:1px 6px;border-radius:3px;cursor:help}
.drag-ok{color:#64748b}
.error-row{margin-top:6px;display:flex;justify-content:space-between;align-items:center;gap:6px}
.err-text{font-size:11px;color:#fca5a5}
.empty{color:#64748b;font-size:12px;padding:6px 0}
</style>
