import asyncio, math, random, time, json, threading
from collections import defaultdict, deque
from queue import Queue, Empty
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np

app = FastAPI(title="Digital Twin Factory Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DEVICE_TYPES = ["CNC", "RobotArm", "Conveyor", "AGV", "InjectionMolding", "QCStation"]
STATUSES = ["RUNNING", "IDLE", "FAULT", "OFFLINE"]
ACTIVE_CLIENTS: list[WebSocket] = []
SIMULATOR_RUNNING = True

class DeviceState:
    def __init__(self, did: int, dtype: str, x: float, y: float, z: float):
        self.id = did
        self.type = dtype
        self.status = "RUNNING"
        self.position = [x, y, z]
        self.temperature = random.uniform(35, 45)
        self.vibration = random.uniform(0.1, 1.5)
        self.pressure = random.uniform(0.8, 1.2)
        self.production_count = 0
        self.fault_count = 0
        self.uptime = 0.0
        self.cycle_time = random.uniform(2, 8)
        self.quality_rate = random.uniform(0.95, 0.995)

    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "position": self.position, "temperature": round(self.temperature, 2),
            "vibration": round(self.vibration, 3), "pressure": round(self.pressure, 2),
            "production_count": self.production_count, "fault_count": self.fault_count,
            "uptime": round(self.uptime, 2), "quality_rate": round(self.quality_rate, 3)
        }

devices = {i: DeviceState(i, random.choice(DEVICE_TYPES),
                          random.uniform(-5, 5), 0.5, random.uniform(-5, 5)) for i in range(1, 13)}

production_log = []
anomaly_log = []

class AnomalyRules:
    def __init__(self):
        self.rules = [
            {"name": "高温告警", "field": "temperature", "threshold": 48, "op": "gt"},
            {"name": "振动超标", "field": "vibration", "threshold": 2.0, "op": "gt"},
            {"name": "压力异常", "field": "pressure", "threshold": 1.5, "op": "gt"},
        ]
        self.windows = defaultdict(lambda: deque(maxlen=10))

    def check(self, dev: DeviceState):
        triggers = []
        for rule in self.rules:
            val = getattr(dev, rule["field"])
            if (rule["op"] == "gt" and val > rule["threshold"]) or (rule["op"] == "lt" and val < rule["threshold"]):
                triggers.append({"device_id": dev.id, "rule": rule["name"],
                                 "value": round(val, 3), "threshold": rule["threshold"]})

        # sliding window trend
        key = f"{dev.id}_temp"
        self.windows[key].append(dev.temperature)
        if len(self.windows[key]) >= 8:
            vals = list(self.windows[key])
            if np.mean(vals[-4:]) - np.mean(vals[:4]) > 3:
                triggers.append({"device_id": dev.id, "rule": "温度趋势上升", "value": round(np.mean(vals[-4:]), 2), "threshold": ">3°C/周期"})

        if triggers:
            anomaly_log.append({"timestamp": time.time(), "triggers": triggers, "device_type": dev.type})
        return triggers

rules_engine = AnomalyRules()

def simulate():
    while SIMULATOR_RUNNING:
        for dev in devices.values():
            drift = 0.1 * math.sin(time.time() * 0.5 + dev.id)
            noise = random.gauss(0, 0.3)
            dev.temperature = max(25, min(65, dev.temperature + drift + noise))

            v_drift = 0.02 * math.sin(time.time() * 0.3 + dev.id * 0.7)
            dev.vibration = max(0, min(3, dev.vibration + v_drift + random.gauss(0, 0.05)))

            dev.pressure = max(0.5, min(2, dev.pressure + random.gauss(0, 0.02)))

            if random.random() < 0.015:
                dev.status = "FAULT"
                dev.fault_count += 1
            elif random.random() < 0.03 and dev.status == "FAULT":
                dev.status = "RUNNING"

            if dev.status == "RUNNING":
                if random.random() < 0.4:
                    dev.production_count += 1
                dev.uptime += 1

            triggers = rules_engine.check(dev)
            if triggers and dev.status != "FAULT" and random.random() < 0.3:
                dev.status = "FAULT"

        production_log.append({"timestamp": time.time(), "count": sum(d.production_count for d in devices.values())})

        try:
            payload = {
                "devices": [d.to_dict() for d in devices.values()],
                "production": sum(d.production_count for d in devices.values()),
                "anomalies": anomaly_log[-5:] if anomaly_log else [],
                "oee": calculate_oee()
            }
            msg = json.dumps(payload)
        except:
            continue

        dead = []
        for ws in ACTIVE_CLIENTS:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), asyncio.get_event_loop())
            except:
                dead.append(ws)
        for ws in dead:
            if ws in ACTIVE_CLIENTS:
                ACTIVE_CLIENTS.remove(ws)

        time.sleep(1)


def calculate_oee():
    oee_list = []
    for dev in devices.values():
        if dev.uptime == 0:
            continue
        availability = availability_ratio(dev)  # 与健康度在线率同一口径
        performance = min(1.0, dev.production_count / max(1, dev.uptime / 2))
        quality = dev.quality_rate
        oee = round(availability * performance * quality * 100, 1)
        oee_list.append({"id": dev.id, "type": dev.type, "oee": oee,
                         "availability": round(availability * 100, 1),
                         "performance": round(performance * 100, 1),
                         "quality": round(quality * 100, 1)})
    return oee_list


class OEEAnalysis(BaseModel):
    availability: float
    performance: float
    quality: float


# ==================== 设备健康度评分（统一口径） ====================
# 健康评分与在线率的计算规则只有这一份实现：实时展示、整组重算、单台重试
# 全部走 evaluate_health()，并在结果中带 formula_version 标识口径版本，
# 保证队列重算与上一轮评分口径一致。
HEALTH_FORMULA_VERSION = "v1.0"


def availability_ratio(dev: DeviceState) -> float:
    """在线率口径与 calculate_oee 中的 availability 完全一致。"""
    return min(1.0, dev.uptime / max(1, dev.uptime + dev.fault_count))


def snapshot_device(dev: DeviceState) -> dict:
    """入队时定格设备遥测，排队等待期间模拟器继续漂移也不影响本轮结果。"""
    return {
        "id": dev.id, "type": dev.type, "status": dev.status,
        "temperature": round(dev.temperature, 2),
        "vibration": round(dev.vibration, 3),
        "pressure": round(dev.pressure, 2),
        "fault_count": dev.fault_count, "uptime": round(dev.uptime, 2),
        "quality_rate": round(dev.quality_rate, 3),
    }


def evaluate_health(snap: dict) -> dict:
    """按 HEALTH_FORMULA_VERSION 对一份遥测快照打分。纯函数，可独立测试。"""
    penalties = []

    t = snap["temperature"]
    if t >= 55:
        penalties.append(("温度过高", 30, f"{t:.1f}°C ≥ 55°C"))
    elif t >= 48:
        penalties.append(("温度偏高", 15, f"{t:.1f}°C ≥ 48°C"))
    elif t <= 25:
        penalties.append(("温度偏低", 8, f"{t:.1f}°C ≤ 25°C"))

    v = snap["vibration"]
    if v >= 2.5:
        penalties.append(("振动严重超标", 25, f"{v:.2f}mm/s ≥ 2.5"))
    elif v >= 2.0:
        penalties.append(("振动超标", 12, f"{v:.2f}mm/s ≥ 2.0"))

    p = snap["pressure"]
    if p >= 1.8:
        penalties.append(("压力严重异常", 20, f"{p:.2f} ≥ 1.8"))
    elif p >= 1.5:
        penalties.append(("压力异常", 10, f"{p:.2f} ≥ 1.5"))
    elif p <= 0.6:
        penalties.append(("压力偏低", 10, f"{p:.2f} ≤ 0.6"))

    fc = snap["fault_count"]
    if fc >= 5:
        penalties.append(("历史故障过多", 15, f"累计故障 {fc} 次"))
    elif fc >= 2:
        penalties.append(("历史故障偏多", 8, f"累计故障 {fc} 次"))

    if snap["status"] == "FAULT":
        penalties.append(("当前故障停机", 20, "设备状态 FAULT"))
    elif snap["status"] == "OFFLINE":
        penalties.append(("设备离线", 12, "设备状态 OFFLINE"))
    elif snap["status"] == "IDLE":
        penalties.append(("设备空闲", 5, "设备状态 IDLE"))

    qr = snap["quality_rate"]
    if qr < 0.93:
        penalties.append(("合格率偏低", 12, f"合格率 {(qr*100):.1f}% < 93%"))
    elif qr < 0.96:
        penalties.append(("合格率一般", 5, f"合格率 {(qr*100):.1f}% < 96%"))

    online = min(1.0, snap["uptime"] / max(1, snap["uptime"] + snap["fault_count"]))
    if online < 0.7:
        penalties.append(("在线率过低", 10, f"在线率 {online*100:.1f}% < 70%"))
    elif online < 0.9:
        penalties.append(("在线率偏低", 5, f"在线率 {online*100:.1f}% < 90%"))

    deductions = [
        {"label": label, "penalty": w, "detail": detail}
        for label, w, detail in sorted(penalties, key=lambda x: x[1], reverse=True)
    ]
    score = round(max(0.0, min(100.0, 100.0 - sum(d["penalty"] for d in deductions))), 1)
    return {
        "device_id": snap["id"], "device_type": snap["type"],
        "health_score": score,
        "online_rate": round(online * 100, 1),
        "deductions": deductions,
        "top_deductions": deductions[:3],
        "snapshot": snap,
        "formula_version": HEALTH_FORMULA_VERSION,
        "computed_at": round(time.time(), 2),
    }


# ==================== 健康度排队重算 ====================
# 演示用：健康度子服务首算偶发超时（设备 #6 的第一次尝试必失败，其余成功），
# 重试一律成功。真实环境替换 recalc_worker() 内的失败判定即可。
RETRY_ALWAYS_FAILS = False
SIMULATED_LATENCY = (0.4, 0.9)   # 每台重算耗时（秒），界面能看到进度推进
RETAIN_ROUNDS = 10               # 状态接口最多保留的已完成轮次


class HealthJob:
    def __init__(self, device_id: int, round_id: int, snap: dict, attempt: int = 1):
        self.device_id = device_id
        self.round_id = round_id
        self.snapshot = snap
        self.attempt = attempt
        self.status = "QUEUED"      # QUEUED / RUNNING / SUCCESS / FAILED
        self.result = None
        self.error = None
        self.updated_at = round(time.time(), 2)

    def to_dict(self):
        return {
            "device_id": self.device_id, "round_id": self.round_id,
            "attempt": self.attempt, "status": self.status,
            "result": self.result, "error": self.error,
            "updated_at": self.updated_at,
        }


state_lock = threading.RLock()
recalc_q: "Queue[HealthJob]" = Queue()
recalc_round_seq = 0
recalc_jobs: dict[tuple[int, int], HealthJob] = {}   # (round_id, device_id) -> job
active_rounds: list[dict] = []                        # 进行中轮次 {"id", "status", "device_ids", "current_device_id"}
round_history: list[dict] = []                        # 已完成轮次元数据（最新在前）
last_round_device_ids: set[int] = set()               # 上一轮（最近完成）重算范围


def _round_summary(r: dict) -> dict:
    jobs = [recalc_jobs[(r["id"], did)].to_dict() for did in r["device_ids"]]
    done = sum(1 for j in jobs if j["status"] in ("SUCCESS", "FAILED"))
    ok = sum(1 for j in jobs if j["status"] == "SUCCESS")
    failed = sum(1 for j in jobs if j["status"] == "FAILED")
    return {
        "id": r["id"], "status": r["status"], "total": len(jobs),
        "completed": done, "succeeded": ok, "failed": failed,
        "progress": round(done * 100 / len(jobs), 1) if jobs else 0.0,
        "current_device_id": r.get("current_device_id"),
        "formula_version": HEALTH_FORMULA_VERSION,
        "jobs": jobs,
    }


def recalc_worker():
    """串行消费队列：一次只重算一台，进度可观察。"""
    while SIMULATOR_RUNNING:
        try:
            job = recalc_q.get(timeout=0.5)
        except Empty:
            continue
        try:
            with state_lock:
                job.status = "RUNNING"
                job.updated_at = round(time.time(), 2)
                rnd = next((r for r in active_rounds if r["id"] == job.round_id), None)
                if rnd is not None:
                    rnd["current_device_id"] = job.device_id

            # 模拟调用健康度评分服务的网络耗时
            time.sleep(random.uniform(*SIMULATED_LATENCY))

            fail = RETRY_ALWAYS_FAILS or (job.attempt == 1 and job.device_id == 6)
            if fail:
                raise TimeoutError("健康度评分服务响应超时（HTTP 504），请稍后单台重试")

            with state_lock:
                job.result = evaluate_health(job.snapshot)
                job.status = "SUCCESS"
                job.error = None
                job.updated_at = round(time.time(), 2)
        except Exception as exc:
            with state_lock:
                job.status = "FAILED"
                job.error = str(exc)
                job.updated_at = round(time.time(), 2)
        finally:
            recalc_q.task_done()
            with state_lock:
                rnd = next((r for r in active_rounds if r["id"] == job.round_id), None)
                if rnd is not None:
                    jobs = [recalc_jobs[(rnd["id"], did)] for did in rnd["device_ids"]]
                    if all(j.status in ("SUCCESS", "FAILED") for j in jobs):
                        rnd["status"] = "DONE"
                        rnd["current_device_id"] = None
                        global last_round_device_ids
                        last_round_device_ids = set(rnd["device_ids"])
                        # 只存元数据，状态查询时按最新 job 重新汇总，单台重试结果也能反映
                        round_history.insert(0, dict(rnd))
                        del round_history[RETAIN_ROUNDS:]
                        active_rounds.remove(rnd)
                        # 清理掉早已不在历史窗口内的 job 记录
                        keep_rounds = {r["id"] for r in active_rounds} | \
                                      {r["id"] for r in round_history}
                        for k in [k for k in recalc_jobs if k[0] not in keep_rounds]:
                            del recalc_jobs[k]
                    else:
                        rnd["current_device_id"] = None
                else:
                    # 该轮已结束，这是一次单台重试
                    hist = next((r for r in round_history if r["id"] == job.round_id), None)
                    if hist is not None:
                        jobs = [recalc_jobs[(hist["id"], did)] for did in hist["device_ids"]]
                        if all(j.status in ("SUCCESS", "FAILED") for j in jobs):
                            hist["status"] = "DONE"
                            hist["current_device_id"] = None
                        else:
                            hist["current_device_id"] = None


class RecalcEnqueueRequest(BaseModel):
    device_ids: list[int]


class RetryRequest(BaseModel):
    round_id: int
    device_id: int


@app.post("/api/health/recalc/enqueue")
def enqueue_recalc(req: RecalcEnqueueRequest):
    """整组设备加入重算队列。重复入队 / 与上一轮范围重叠的设备会被指出并跳过。"""
    global recalc_round_seq

    accepted: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()

    with state_lock:
        # 检查与入队在同一把锁内完成，并发请求也不会让同一台设备重复入队
        queued_ids = {j.device_id for j in list(recalc_q.queue)}
        running_ids = {j.device_id for j in recalc_jobs.values() if j.status == "RUNNING"}

        for did in req.device_ids:
            if did in seen:
                skipped.append({"device_id": did, "reason": "本次请求内重复入队，已跳过"})
                continue
            seen.add(did)
            if did not in devices:
                skipped.append({"device_id": did, "reason": "设备不存在，已跳过"})
            elif did in queued_ids or did in running_ids:
                skipped.append({"device_id": did, "reason": "该设备已在当前重算队列中，不能重复入队，已跳过"})
            elif did in last_round_device_ids:
                skipped.append({"device_id": did, "reason": "与上一轮重算范围重叠，已跳过"})
            else:
                accepted.append(did)

        if accepted:
            recalc_round_seq += 1
            round_id = recalc_round_seq
            snaps = {did: snapshot_device(devices[did]) for did in accepted}
            rnd = {"id": round_id, "status": "RUNNING", "device_ids": list(accepted),
                   "current_device_id": None}
            active_rounds.append(rnd)
            for did in accepted:
                job = HealthJob(did, round_id, snaps[did])
                recalc_jobs[(round_id, did)] = job
                recalc_q.put(job)

    if not accepted:
        return {"round_id": None, "accepted": [], "skipped": skipped,
                "formula_version": HEALTH_FORMULA_VERSION,
                "message": "没有可入队的设备，全部被跳过"}

    msg = f"已将 {len(accepted)} 台设备加入第 {round_id} 轮重算队列"
    if skipped:
        msg += f"；另有 {len(skipped)} 台被跳过（详见 skipped）"
    return {"round_id": round_id, "accepted": accepted, "skipped": skipped,
            "formula_version": HEALTH_FORMULA_VERSION, "message": msg}


@app.post("/api/health/recalc/retry")
def retry_recalc(req: RetryRequest):
    """对某轮中失败的单台设备重试。重试沿用入队时定格的同一快照，口径与结果可复现。"""
    key = (req.round_id, req.device_id)
    with state_lock:
        job = recalc_jobs.get(key)
        if job is None:
            raise HTTPException(status_code=404,
                                detail=f"找不到第 {req.round_id} 轮中设备 #{req.device_id} 的重算记录")
        # 同一台设备不能重复入队
        if job.status == "QUEUED" or job.status == "RUNNING" or \
                any(j.device_id == req.device_id and j.round_id == req.round_id
                    for j in list(recalc_q.queue)):
            raise HTTPException(status_code=409,
                                detail=f"设备 #{req.device_id} 的重算已在队列中，请勿重复提交")
        if job.status != "FAILED":
            raise HTTPException(status_code=409,
                                detail=f"设备 #{req.device_id} 当前状态为 {job.status}，无需重试")
        job.attempt += 1
        job.status = "QUEUED"
        job.error = None
        job.updated_at = round(time.time(), 2)
        attempt = job.attempt
        hist = next((r for r in round_history if r["id"] == req.round_id), None)
        if hist is not None:
            hist["status"] = "RETRYING"
            hist["current_device_id"] = req.device_id
        recalc_q.put(job)
    return {"round_id": req.round_id, "device_id": req.device_id,
            "attempt": attempt, "formula_version": HEALTH_FORMULA_VERSION,
            "message": f"设备 #{req.device_id} 的第 {attempt} 次重算已加入队列"}


@app.get("/api/health/recalc/status")
def recalc_status():
    """队列进度：当前轮次、已完成台数、逐条结果（含主要拖低项）。"""
    with state_lock:
        return {
            "formula_version": HEALTH_FORMULA_VERSION,
            "queue_size": recalc_q.qsize(),
            "active_rounds": [_round_summary(r) for r in active_rounds],
            "history": [_round_summary(r) for r in round_history],
            "last_round_device_ids": sorted(last_round_device_ids),
        }


@app.on_event("startup")
async def startup():
    t = threading.Thread(target=simulate, daemon=True)
    t.start()
    threading.Thread(target=recalc_worker, daemon=True).start()


@app.get("/api/devices")
def get_devices():
    return {"devices": [d.to_dict() for d in devices.values()], "anomalies": anomaly_log[-10:]}


@app.get("/api/oee")
def get_oee():
    return {"oee": calculate_oee()}


@app.get("/api/production")
def get_production():
    return {"log": production_log[-60:]}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_CLIENTS.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in ACTIVE_CLIENTS:
            ACTIVE_CLIENTS.remove(websocket)


@app.on_event("shutdown")
async def shutdown():
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = False