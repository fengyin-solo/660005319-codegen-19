<template>
  <table class="job-table">
    <thead>
      <tr>
        <th>设备</th>
        <th>状态</th>
        <th>健康评分</th>
        <th>在线率</th>
        <th class="deduct-col">主要拖低项 / 异常说明</th>
        <th>操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="job in round.jobs" :key="job.device_id">
        <td>#{{ job.device_id }}<span v-if="job.result" class="j-type">{{ job.result.device_type }}</span></td>
        <td><span class="st" :class="statusClass(job)">{{ statusText(job) }}</span></td>
        <td>
          <span v-if="job.result" class="j-score" :style="{ color: healthScoreColor(job.result.health_score) }">
            {{ job.result.health_score.toFixed(1) }}
          </span>
          <span v-else>—</span>
        </td>
        <td>{{ job.result ? job.result.online_rate.toFixed(1) + '%' : '—' }}</td>
        <td class="deduct-cell">
          <div v-if="job.result" class="deduct-tags">
            <template v-if="job.result.top_deductions.length">
              <span v-for="d in job.result.top_deductions" :key="d.label" class="deduct-tag" :title="d.detail">
                {{ d.label }} -{{ d.penalty }}
              </span>
            </template>
            <span v-else class="deduct-ok">无明显拖低项</span>
          </div>
          <span v-else-if="job.error" class="job-err" :title="job.error">{{ job.error }}</span>
          <span v-else>—</span>
        </td>
        <td>
          <button v-if="job.status === 'FAILED'" class="retry-btn" @click="$emit('retry', job)">
            单台重试<span v-if="job.attempt > 1">（第 {{ job.attempt }} 次尝试后）</span>
          </button>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
import { healthScoreColor } from '../types'
import type { RecalcRound, RecalcJob } from '../types'

defineProps<{ round: RecalcRound }>()
defineEmits<{ (e: 'retry', job: RecalcJob): void }>()

function statusText(job: RecalcJob): string {
  switch (job.status) {
    case 'QUEUED': return `排队中(第${job.attempt}次)`
    case 'RUNNING': return '重算中…'
    case 'SUCCESS': return '完成'
    case 'FAILED': return '接口异常'
  }
}

function statusClass(job: RecalcJob): string {
  return { QUEUED: 'st-queued', RUNNING: 'st-running', SUCCESS: 'st-ok', FAILED: 'st-fail' }[job.status]
}
</script>

<style scoped>
.job-table{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px}
.job-table th{color:#64b5f6;text-align:left;padding:4px 8px;font-weight:500;border-bottom:1px solid #1e3a5f}
.job-table td{padding:5px 8px;border-bottom:1px solid #112233;color:#cbd5e1;vertical-align:top}
.j-type{color:#64748b;margin-left:4px}
.deduct-col{width:38%}
.j-score{font-weight:700}
.deduct-tags{display:flex;gap:4px;flex-wrap:wrap}
.deduct-tag{background:#7c2d1233;border:1px solid #c2410c55;color:#fdba74;padding:1px 6px;border-radius:3px;white-space:nowrap}
.deduct-ok{color:#2ecc71}
.job-err{color:#fca5a5}
.st{padding:1px 6px;border-radius:3px;font-size:10px;white-space:nowrap}
.st-queued{background:#1e3a5f;color:#93c5fd}
.st-running{background:#78350f55;color:#fbbf24}
.st-ok{background:#14532d55;color:#2ecc71}
.st-fail{background:#7f1d1d55;color:#fca5a5}
.retry-btn{background:transparent;border:1px solid #f59e0b;color:#fbbf24;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer}
.retry-btn:hover{background:#f59e0b22}
</style>
