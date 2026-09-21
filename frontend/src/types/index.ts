export interface Device {
  id: number; type: string; status: string; position: number[]
  temperature: number; vibration: number; pressure: number
  production_count: number; fault_count: number
  uptime: number; quality_rate: number; online_rate?: number
}

export interface Anomaly {
  timestamp: number; triggers: { device_id: number; rule: string; value: number; threshold: string }[]
  device_type: string
}

export interface OEEItem {
  id: number; type: string; oee: number
  availability: number; performance: number; quality: number
}

export interface FactoryData {
  devices: Device[]
  production: number
  anomalies: Anomaly[]
  oee: OEEItem[]
}

// ---- 设备健康度 ----

export interface HealthFactorMeta { key: string; label: string; weight: number }

export interface HealthDrag {
  key: string
  label: string
  sub_score: number
  weight: number
  loss: number
  detail: string
}

export interface HealthItem {
  id: number
  type: string
  health: number
  online_rate: number
  status: string
  status_label: string
  sub_scores: Record<string, number>
  main_drags: HealthDrag[]
  rubric_version: string
  computed_at: number
  source?: 'recalc' | 'realtime'
}

export interface HealthOverview {
  rubric_version: string
  factors: HealthFactorMeta[]
  items: HealthItem[]
  last_round_device_ids: number[]
  recalc_device_ids: number[]
}

export type QueueItemStatus = 'pending' | 'running' | 'success' | 'failed'

export interface QueueItem {
  item_id: string
  device_id: number
  status: QueueItemStatus
  result: HealthItem | null
  error: string | null
  attempts: number
  created_at: number
  finished_at: number | null
}

export interface QueueBatch {
  batch_id: string
  status: 'queued' | 'running' | 'success' | 'failed' | 'partial'
  created_at: number
  counts: { total: number; pending: number; running: number; success: number; failed: number; completed: number }
  current_device_id: number | null
  items: QueueItem[]
}

export interface RecalcSkipped {
  duplicate_in_request: number[]
  duplicate_in_queue: number[]
  overlap_last_round: number[]
  invalid: number[]
}

export interface RecalcResponse {
  batch_id: string | null
  accepted: number[]
  skipped: RecalcSkipped
  message: string
}

export interface QueueTotals {
  total: number; completed: number; success: number; failed: number; running: number; pending: number
}

export interface QueueStatus {
  active: QueueBatch[]
  finished: { batch_id: string; status: string; counts: QueueTotals; created_at: number }[]
  totals: QueueTotals
  current_device_id: number | null
  queued_device_ids: number[]
}

export const DEVICE_COLORS: Record<string, string> = {
  CNC: '#e74c3c', RobotArm: '#3498db', Conveyor: '#f39c12',
  AGV: '#2ecc71', InjectionMolding: '#9b59b6', QCStation: '#1abc9c'
}

export const STATUS_COLORS: Record<string, string> = {
  RUNNING: '#2ecc71', IDLE: '#f1c40f', FAULT: '#e74c3c', OFFLINE: '#95a5a6'
}

// 健康度颜色沿用现有配色家族（绿/黄/红），不改动原有状态颜色
export const HEALTH_COLORS: Record<'good' | 'warn' | 'bad', string> = {
  good: '#2ecc71',
  warn: '#f1c40f',
  bad: '#e74c3c'
}

export function healthLevel(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 80) return 'good'
  if (score >= 60) return 'warn'
  return 'bad'
}

export const DEVICE_TYPE_LABELS: Record<string, string> = {
  CNC: 'CNC机床', RobotArm: '机械臂', Conveyor: '传送带',
  AGV: 'AGV小车', InjectionMolding: '注塑机', QCStation: '质检站'
}