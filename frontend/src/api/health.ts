import axios from 'axios'
import type {
  HealthOverview, QueueStatus, QueueBatch, RecalcResponse,
} from '@/types'

const http = axios.create({ timeout: 15000 })

export function errMessage(e: unknown, fallback = '请求失败，请稍后重试'): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: string } | undefined)?.detail
    if (detail) return detail
    if (e.code === 'ECONNABORTED') return '请求超时，后端服务可能繁忙'
    if (e.response) return `服务异常（HTTP ${e.response.status}）`
    return '网络异常，无法连接后端服务'
  }
  return e instanceof Error ? e.message : fallback
}

export const getHealth = () =>
  http.get<HealthOverview>('/api/health').then(r => r.data)

export const enqueueRecalc = (deviceIds: number[], newRound = false) =>
  http.post<RecalcResponse>('/api/health/recalc', {
    device_ids: deviceIds, new_round: newRound,
  }).then(r => r.data)

export const getQueue = () =>
  http.get<QueueStatus>('/api/health/queue').then(r => r.data)

export const getBatch = (batchId: string) =>
  http.get<QueueBatch>(`/api/health/batches/${batchId}`).then(r => r.data)

export const retryDevice = (deviceId: number, itemId?: string) =>
  http.post<{ result: HealthOverview['items'][number]; message: string }>(
    '/api/health/retry',
    { device_id: deviceId, item_id: itemId ?? null },
  ).then(r => r.data)
