<template>
  <div class="health-panel">
    <div class="hp-head">
      <h4>🩺 设备健康度排队重算</h4>
      <el-tag size="small" type="info" effect="dark">评分口径 {{ queue.status?.formula_version || '—' }}</el-tag>
      <el-tag size="small" type="info" effect="plain">队列 {{ queue.status?.queue_size ?? 0 }} 台</el-tag>
      <el-tag v-if="overlapHint" size="small" type="warning" effect="plain">上一轮范围: {{ overlapHint }}</el-tag>
    </div>

    <div class="hp-controls">
      <el-select
        v-model="selected"
        multiple
        collapse-tags
        collapse-tags-tooltip
        placeholder="选择需要整组重算的设备（可多选）"
        class="hp-select"
      >
        <el-option
          v-for="d in devices"
          :key="d.id"
          :label="`#${d.id} ${d.type}（${d.status}）`"
          :value="d.id"
        />
      </el-select>
      <el-button type="primary" :loading="queue.loading" @click="submitEnqueue">
        加入队列重算
      </el-button>
      <el-button @click="selectAll">全选</el-button>
      <el-button @click="selected = []">清空</el-button>
    </div>

    <div v-if="queue.lastError" class="hp-api-error">
      ⚠️ {{ queue.lastError }}
    </div>

    <div v-if="activeRounds.length" class="hp-rounds">
      <div v-for="r in activeRounds" :key="r.id" class="hp-round">
        <div class="hp-round-head">
          <span class="hp-round-title">第 {{ r.id }} 轮重算进行中</span>
          <span class="hp-count">已完成 {{ r.completed }} / {{ r.total }} 台
            （成功 {{ r.succeeded }}，失败 {{ r.failed }}）</span>
          <el-tag v-if="r.current_device_id" size="small" type="warning">
            正在重算 #{{ r.current_device_id }}
          </el-tag>
        </div>
        <el-progress :percentage="r.progress" :stroke-width="10" />
        <RecalcJobTable :round="r" @retry="onRetry" />
      </div>
    </div>

    <template v-else-if="history.length">
      <div v-for="r in history.slice(0, 3)" :key="r.id" class="hp-round">
        <div class="hp-round-head">
          <span class="hp-round-title">第 {{ r.id }} 轮重算结果</span>
          <span class="hp-count">已完成 {{ r.completed }} / {{ r.total }} 台
            （成功 {{ r.succeeded }}，失败 {{ r.failed }}）</span>
          <el-tag v-if="r.status === 'RETRYING'" size="small" type="warning">单台重试中…</el-tag>
          <el-tag v-else size="small" type="success">已完成</el-tag>
        </div>
        <el-progress :percentage="r.progress" :stroke-width="10" />
        <RecalcJobTable :round="r" @retry="onRetry" />
      </div>
    </template>

    <div v-else class="hp-empty">暂无重算记录，勾选设备后可整组加入队列。</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, onUnmounted } from 'vue'
import { useFactoryStore } from '../store/factory'
import { useHealthQueueStore } from '../store/healthQueue'
import RecalcJobTable from './RecalcJobTable.vue'
import type { RecalcJob } from '../types'

const factory = useFactoryStore()
const queue = useHealthQueueStore()
const selected = ref<number[]>([])

const devices = computed(() => factory.data?.devices || [])
const activeRounds = computed(() => queue.status?.active_rounds || [])
const history = computed(() => queue.status?.history || [])
const overlapHint = computed(() => (queue.status?.last_round_device_ids || []).map(id => `#${id}`).join('、'))

function selectAll() {
  selected.value = devices.value.map(d => d.id)
}

async function submitEnqueue() {
  if (!selected.value.length) {
    ElMessage.warning('请先至少选择一台设备')
    return
  }
  const resp = await queue.enqueue(selected.value)
  if (!resp) return // 接口异常说明已在面板顶部展示
  if (resp.skipped.length) {
    await ElMessageBox.alert(
      h('div', null, [
        h('p', { style: 'margin:0 0 8px' }, resp.message),
        h('ul', { style: 'margin:0;padding-left:18px' },
          resp.skipped.map(s => h('li', null, `设备 #${s.device_id}：${s.reason}`))),
      ]),
      '部分设备被跳过',
      { confirmButtonText: '知道了' },
    )
  } else if (resp.round_id !== null) {
    ElMessage.success(resp.message)
  } else {
    ElMessage.warning(resp.message)
  }
  selected.value = []
}

async function onRetry(job: RecalcJob) {
  const ok = await queue.retry(job.round_id, job.device_id)
  if (ok) ElMessage.success(`设备 #${job.device_id} 已加入重试队列`)
  else ElMessage.error(queue.lastError || '重试失败')
}

onMounted(() => queue.startPolling())
onUnmounted(() => queue.stopPolling())
</script>

<style scoped>
.health-panel{background:#0d1b2a;border-radius:8px;padding:12px;border:1px solid #1e3a5f}
.hp-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.hp-head h4{color:#64b5f6;font-size:13px;margin:0}
.hp-controls{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.hp-select{width:360px}
.hp-api-error{background:#7f1d1d33;border:1px solid #7f1d1d55;color:#fca5a5;
  font-size:12px;padding:6px 10px;border-radius:4px;margin-bottom:10px}
.hp-rounds{display:flex;flex-direction:column;gap:12px}
.hp-round{border-top:1px dashed #1e3a5f;padding-top:10px}
.hp-round-head{display:flex;align-items:center;gap:12px;margin-bottom:6px;font-size:12px;flex-wrap:wrap}
.hp-round-title{color:#e0e6ed;font-weight:600}
.hp-count{color:#94a3b8}
.hp-empty{color:#64748b;font-size:12px;padding:8px 0}
</style>
