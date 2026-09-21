import { defineStore } from 'pinia'
import { ref, onUnmounted } from 'vue'
import axios from 'axios'
import type { RecalcStatus, EnqueueResponse } from '@/types'

/**
 * 健康度排队重算 store。
 * 接口异常时把 HTTP 状态码与后端 detail 原样带出来，界面负责给出说明；
 * 不吞错、不自动重试（单台重试由用户显式触发）。
 */
export const useHealthQueueStore = defineStore('healthQueue', () => {
  const status = ref<RecalcStatus | null>(null)
  const loading = ref(false)
  const lastError = ref<string | null>(null)
  let timer: number | null = null

  function explain(err: unknown): string {
    if (axios.isAxiosError(err)) {
      const detail = err.response?.data?.detail
      if (detail) return `接口返回 ${err.response?.status}：${detail}`
      if (err.code === 'ECONNABORTED') return '请求超时，请稍后重试'
      return `网络异常（${err.message}），请检查后端服务`
    }
    return err instanceof Error ? err.message : String(err)
  }

  async function refresh() {
    try {
      const { data } = await axios.get<RecalcStatus>('/api/health/recalc/status')
      status.value = data
      lastError.value = null
    } catch (err) {
      lastError.value = explain(err)
    }
  }

  async function enqueue(deviceIds: number[]): Promise<EnqueueResponse | null> {
    loading.value = true
    try {
      const { data } = await axios.post<EnqueueResponse>('/api/health/recalc/enqueue', {
        device_ids: deviceIds,
      })
      lastError.value = null
      await refresh()
      return data
    } catch (err) {
      lastError.value = explain(err)
      return null
    } finally {
      loading.value = false
    }
  }

  async function retry(roundId: number, deviceId: number): Promise<boolean> {
    try {
      await axios.post('/api/health/recalc/retry', {
        round_id: roundId,
        device_id: deviceId,
      })
      lastError.value = null
      await refresh()
      return true
    } catch (err) {
      lastError.value = explain(err)
      return false
    }
  }

  function startPolling() {
    if (timer !== null) return
    refresh()
    timer = window.setInterval(refresh, 1200)
  }

  function stopPolling() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stopPolling)

  return { status, loading, lastError, refresh, enqueue, retry, startPolling, stopPolling }
})
