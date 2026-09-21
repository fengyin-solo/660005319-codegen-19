export interface Device {
  id: number; type: string; status: string; position: number[]
  temperature: number; vibration: number; pressure: number
  production_count: number; fault_count: number
  uptime: number; quality_rate: number
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

export const DEVICE_COLORS: Record<string, string> = {
  CNC: '#e74c3c', RobotArm: '#3498db', Conveyor: '#f39c12',
  AGV: '#2ecc71', InjectionMolding: '#9b59b6', QCStation: '#1abc9c'
}

export const STATUS_COLORS: Record<string, string> = {
  RUNNING: '#2ecc71', IDLE: '#f1c40f', FAULT: '#e74c3c', OFFLINE: '#95a5a6'
}

// ===== 健康度排队重算 =====
export interface HealthDeduction {
  label: string
  penalty: number
  detail: string
}

export interface HealthResult {
  device_id: number
  device_type: string
  health_score: number
  online_rate: number
  deductions: HealthDeduction[]
  top_deductions: HealthDeduction[]
  snapshot: Record<string, number | string>
  formula_version: string
  computed_at: number
}

export type RecalcJobStatus = 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED'

export interface RecalcJob {
  device_id: number
  round_id: number
  attempt: number
  status: RecalcJobStatus
  result: HealthResult | null
  error: string | null
  updated_at: number
}

export interface RecalcRound {
  id: number
  status: 'RUNNING' | 'DONE' | 'RETRYING'
  total: number
  completed: number
  succeeded: number
  failed: number
  progress: number
  current_device_id: number | null
  formula_version: string
  jobs: RecalcJob[]
}

export interface RecalcStatus {
  formula_version: string
  queue_size: number
  active_rounds: RecalcRound[]
  history: RecalcRound[]
  last_round_device_ids: number[]
}

export interface EnqueueResponse {
  round_id: number | null
  accepted: number[]
  skipped: { device_id: number; reason: string }[]
  formula_version: string
  message: string
}

// 健康分颜色沿用原有面板的绿/黄/红色板，不引入新色
export function healthScoreColor(score: number): string {
  if (score >= 80) return '#2ecc71'
  if (score >= 60) return '#f1c40f'
  return '#e74c3c'
}