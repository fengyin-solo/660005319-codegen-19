import asyncio, math, random, time, json, threading, uuid
from collections import defaultdict, deque
from typing import Optional, List, Dict, Any
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

# ---------------------------------------------------------------------------
# 设备健康度评分口径（版本化）
# 实时查询 /api/health 与排队重算共用 compute_device_health() 同一套口径，
# 保证“与上一轮的评分口径保持一致”。调整口径必须升级 RUBRIC_VERSION。
# ---------------------------------------------------------------------------
RUBRIC_VERSION = "v1.0"
HEALTH_FACTORS = [
    {"key": "temperature", "label": "温度", "weight": 0.20},
    {"key": "vibration", "label": "振动", "weight": 0.20},
    {"key": "pressure", "label": "压力", "weight": 0.15},
    {"key": "fault", "label": "故障频度", "weight": 0.20},
    {"key": "status", "label": "运行状态", "weight": 0.10},
    {"key": "quality", "label": "质量率", "weight": 0.15},
]
FACTOR_LABELS = {f["key"]: f["label"] for f in HEALTH_FACTORS}
FACTOR_WEIGHTS = {f["key"]: f["weight"] for f in HEALTH_FACTORS}
STATUS_LABELS = {"RUNNING": "运行", "IDLE": "待机", "FAULT": "故障", "OFFLINE": "离线"}
# 单台重算调用下游健康分析服务的失败概率（演示接口异常与单台重试）
UPSTREAM_FAIL_RATE = 0.15

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
        # 在线率统计：观测 tick 数与在线（非 OFFLINE）tick 数
        self.observed_ticks = 0
        self.online_ticks = 0

    @property
    def online_rate(self) -> float:
        if self.observed_ticks == 0:
            return 100.0 if self.status != "OFFLINE" else 0.0
        return round(self.online_ticks / self.observed_ticks * 100, 1)

    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "position": self.position, "temperature": round(self.temperature, 2),
            "vibration": round(self.vibration, 3), "pressure": round(self.pressure, 2),
            "production_count": self.production_count, "fault_count": self.fault_count,
            "uptime": round(self.uptime, 2), "quality_rate": round(self.quality_rate, 3),
            "online_rate": self.online_rate
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

            # 状态切换（保持原有 RUNNING/IDLE/FAULT/OFFLINE 语义，补充少量离线与恢复）
            if dev.status == "OFFLINE":
                if random.random() < 0.08:
                    dev.status = "RUNNING"
            else:
                if random.random() < 0.005:
                    dev.status = "OFFLINE"
                elif random.random() < 0.015:
                    dev.status = "FAULT"
                    dev.fault_count += 1
                elif dev.status == "FAULT" and random.random() < 0.03:
                    dev.status = "RUNNING"

            # 在线率观测
            dev.observed_ticks += 1
            if dev.status != "OFFLINE":
                dev.online_ticks += 1

            if dev.status == "RUNNING":
                if random.random() < 0.4:
                    dev.production_count += 1
                dev.uptime += 1

            triggers = rules_engine.check(dev)
            if triggers and dev.status not in ("FAULT", "OFFLINE") and random.random() < 0.3:
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
        availability = min(1.0, dev.uptime / max(1, dev.uptime + dev.fault_count))
        performance = min(1.0, dev.production_count / max(1, dev.uptime / 2))
        quality = dev.quality_rate
        oee = round(availability * performance * quality * 100, 1)
        oee_list.append({"id": dev.id, "type": dev.type, "oee": oee,
                         "availability": round(availability * 100, 1),
                         "performance": round(performance * 100, 1),
                         "quality": round(quality * 100, 1)})
    return oee_list


# ===========================================================================
# 健康度评分与排队重算
# ===========================================================================

def _piecewise_score(value: float, points: List[tuple]) -> float:
    """按 [(阈值, 得分), ...] 分段线性映射，返回 0~100。points 需按阈值升序。"""
    if value <= points[0][0]:
        return float(points[0][1])
    if value >= points[-1][0]:
        return float(points[-1][1])
    for i in range(len(points) - 1):
        x0, s0 = points[i]
        x1, s1 = points[i + 1]
        if x0 <= value <= x1:
            if x1 == x0:
                return float(s1)
            return round(s0 + (s1 - s0) * (value - x0) / (x1 - x0), 1)
    return float(points[-1][1])


def compute_device_health(dev: DeviceState) -> Dict[str, Any]:
    """统一的健康评分口径。实时查询与队列重算都走这里，保证逐轮口径一致。"""
    # 温度：≤40 满分；48 及格线 60；≥60 为 10
    temp_score = _piecewise_score(dev.temperature, [(40, 100), (48, 60), (55, 30), (60, 10)])
    # 振动：≤1.2 满分；2.0 及格线；≥3.0 为 10
    vib_score = _piecewise_score(dev.vibration, [(1.2, 100), (2.0, 60), (2.5, 30), (3.0, 10)])
    # 压力：1.0 附近最佳，双向偏离
    if dev.pressure <= 1.0:
        pres_score = _piecewise_score(dev.pressure, [(0.8, 100), (0.6, 60), (0.5, 30)])
    else:
        pres_score = _piecewise_score(dev.pressure, [(1.2, 100), (1.5, 60), (2.0, 30)])
    # 故障频度：fault_count 占观测时长比例（fault_count/uptime 被放大，uptime 很小时给保守分）
    fault_ratio = dev.fault_count / max(1.0, dev.uptime)
    fault_score = _piecewise_score(fault_ratio, [(0.02, 100), (0.1, 60), (0.3, 30), (0.6, 10)])
    # 运行状态
    status_score = {"RUNNING": 100, "IDLE": 75, "FAULT": 25, "OFFLINE": 10}.get(dev.status, 60)
    # 质量率
    quality_score = _piecewise_score(dev.quality_rate * 100, [(99, 100), (97, 80), (95, 60), (90, 30)])

    sub_scores = {
        "temperature": round(temp_score, 1),
        "vibration": round(vib_score, 1),
        "pressure": round(pres_score, 1),
        "fault": round(fault_score, 1),
        "status": float(status_score),
        "quality": round(quality_score, 1),
    }
    health = round(sum(sub_scores[k] * w for k, w in FACTOR_WEIGHTS.items()), 1)

    # 主要拖低项：按“扣掉的分 = 权重 × (100 − 子项得分)”排序，扣分>=2 分才提示，最多3项
    drags = sorted(
        (
            {
                "key": k,
                "label": FACTOR_LABELS[k],
                "sub_score": sub_scores[k],
                "weight": FACTOR_WEIGHTS[k],
                "loss": round(FACTOR_WEIGHTS[k] * (100 - sub_scores[k]), 1),
                "detail": _factor_detail(k, dev),
            }
            for k in FACTOR_WEIGHTS
        ),
        key=lambda d: d["loss"],
        reverse=True,
    )
    main_drags = [d for d in drags if d["loss"] >= 2.0][:3]
    return {
        "id": dev.id,
        "type": dev.type,
        "health": health,
        "online_rate": dev.online_rate,
        "status": dev.status,
        "status_label": STATUS_LABELS.get(dev.status, dev.status),
        "sub_scores": sub_scores,
        "main_drags": main_drags,
        "rubric_version": RUBRIC_VERSION,
        "computed_at": round(time.time(), 2),
    }


def _factor_detail(key: str, dev: DeviceState) -> str:
    if key == "temperature":
        return f"当前 {dev.temperature:.1f}°C，阈值 48°C"
    if key == "vibration":
        return f"当前 {dev.vibration:.2f}mm/s，阈值 2.0mm/s"
    if key == "pressure":
        return f"当前 {dev.pressure:.2f}MPa，正常区间 0.8~1.2MPa"
    if key == "fault":
        return f"累计故障 {dev.fault_count} 次 / 运行 {dev.uptime:.0f} 拍"
    if key == "status":
        return f"当前状态：{STATUS_LABELS.get(dev.status, dev.status)}"
    if key == "quality":
        return f"质量率 {dev.quality_rate * 100:.1f}%"
    return ""


def health_level(score: float) -> str:
    if score >= 80:
        return "good"
    if score >= 60:
        return "warn"
    return "bad"


# ---- 队列引擎 --------------------------------------------------------------

class QueueItem:
    def __init__(self, device_id: int):
        self.item_id = uuid.uuid4().hex[:12]
        self.device_id = device_id
        self.status = "pending"          # pending / running / success / failed
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        self.attempts = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "device_id": self.device_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "created_at": round(self.created_at, 2),
            "finished_at": round(self.finished_at, 2) if self.finished_at else None,
        }


class Batch:
    def __init__(self, device_ids: List[int]):
        self.batch_id = uuid.uuid4().hex[:12]
        self.items = [QueueItem(d) for d in device_ids]
        self.created_at = time.time()

    @property
    def status(self) -> str:
        st = {i.status for i in self.items}
        if st <= {"success"}:
            return "success"
        if st <= {"failed"}:
            return "failed"
        if "running" in st:
            return "running"
        if "pending" in st:
            return "queued"
        return "partial"  # 已跑完但成败混合

    def counts(self) -> Dict[str, int]:
        c = {"total": len(self.items), "pending": 0, "running": 0, "success": 0, "failed": 0}
        for i in self.items:
            c[i.status] += 1
        c["completed"] = c["success"] + c["failed"]
        return c

    def to_dict(self) -> Dict[str, Any]:
        c = self.counts()
        current = next((i.device_id for i in self.items if i.status == "running"), None)
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "created_at": round(self.created_at, 2),
            "counts": c,
            "current_device_id": current,
            "items": [i.to_dict() for i in self.items],
        }


health_lock = threading.RLock()
health_results: Dict[int, Dict[str, Any]] = {}   # 最近一次重算结果（按设备）
batches: deque = deque(maxlen=50)                 # 最近批次
queued_devices: set = set()                       # pending+running 的设备（全局唯一入队）
last_round_scope: set = set()                     # 上一轮已完成批次的设备范围
queue_wakeup = threading.Condition(health_lock)


def _refresh_queued_devices():
    queued_devices.clear()
    for b in batches:
        for it in b.items:
            if it.status in ("pending", "running"):
                queued_devices.add(it.device_id)


def _run_item(item: QueueItem):
    """模拟调用下游健康分析服务：有概率异常；成功后用统一口径本地计算。"""
    item.status = "running"
    item.attempts += 1
    time.sleep(random.uniform(0.3, 0.8))   # 让队列进度可见
    if random.random() < UPSTREAM_FAIL_RATE:
        raise RuntimeError("健康分析服务暂时不可用（HTTP 503），请稍后单台重试")
    dev = devices.get(item.device_id)
    if dev is None:
        raise RuntimeError(f"设备 #{item.device_id} 不存在或已下线")
    item.result = compute_device_health(dev)


def health_worker():
    while SIMULATOR_RUNNING:
        with queue_wakeup:
            while SIMULATOR_RUNNING:
                item = None
                batch = None
                for b in batches:
                    for it in b.items:
                        if it.status == "pending":
                            item, batch = it, b
                            break
                    if item:
                        break
                if item:
                    break
                queue_wakeup.wait(timeout=1.0)
            if not SIMULATOR_RUNNING:
                return
            target, target_batch = item, batch
        try:
            _run_item(target)
            with health_lock:
                target.status = "success"
                target.finished_at = time.time()
                health_results[target.device_id] = target.result
        except Exception as e:
            with health_lock:
                target.status = "failed"
                target.error = str(e)
                target.finished_at = time.time()
        finally:
            with health_lock:
                _refresh_queued_devices()
                # 批次全部跑完后，以其原始范围作为“上一轮范围”（失败台也算在上一轮内，允许重试/新一轮重算）
                c = target_batch.counts()
                if c["completed"] == c["total"]:
                    last_round_scope.clear()
                    last_round_scope.update(it.device_id for it in target_batch.items)
                queue_wakeup.notify_all()


class RecalcRequest(BaseModel):
    device_ids: List[int]
    new_round: bool = False


class RetryRequest(BaseModel):
    device_id: int
    item_id: Optional[str] = None


@app.on_event("startup")
async def startup():
    t = threading.Thread(target=simulate, daemon=True)
    t.start()
    hw = threading.Thread(target=health_worker, daemon=True)
    hw.start()


@app.get("/api/devices")
def get_devices():
    return {"devices": [d.to_dict() for d in devices.values()], "anomalies": anomaly_log[-10:]}


@app.get("/api/oee")
def get_oee():
    return {"oee": calculate_oee()}


@app.get("/api/production")
def get_production():
    return {"log": production_log[-60:]}


# ---- 设备健康度 ------------------------------------------------------------

@app.get("/api/health")
def get_health():
    """所有设备的健康评分与在线率。优先展示最近一次重算结果，未重算过则按同一口径实时计算。"""
    now_scope = set(health_results.keys())
    items = []
    for dev in devices.values():
        if dev.id in health_results:
            items.append({**health_results[dev.id], "source": "recalc"})
        else:
            items.append({**compute_device_health(dev), "source": "realtime"})
    return {
        "rubric_version": RUBRIC_VERSION,
        "factors": HEALTH_FACTORS,
        "items": items,
        "last_round_device_ids": sorted(last_round_scope),
        "recalc_device_ids": sorted(now_scope),
    }


@app.post("/api/health/recalc")
def enqueue_recalc(req: RecalcRequest):
    """把多台设备整组加入重算队列。
    - 同一台设备不能重复入队（已在 pending/running 中 -> duplicate_in_queue）
    - 与上一轮已完成批次范围重叠 -> overlap_last_round，跳过并指出具体设备
    - new_round=True 表示显式开启新一轮，允许覆盖上一轮范围
    """
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids 不能为空")

    valid, invalid = [], []
    for did in req.device_ids:
        (valid if did in devices else invalid).append(did)

    # 入参自身重复（一次请求里同一台出现多次）
    seen, dup_in_request = set(), []
    for did in valid:
        if did in seen:
            dup_in_request.append(did)
        seen.add(did)
    unique = sorted(seen)

    with health_lock:
        duplicate_in_queue, overlap_last_round, accepted = [], [], []
        for did in unique:
            if did in queued_devices:
                duplicate_in_queue.append(did)
            elif not req.new_round and did in last_round_scope:
                overlap_last_round.append(did)
            else:
                accepted.append(did)

        if not accepted:
            return {
                "batch_id": None,
                "accepted": [],
                "skipped": {
                    "duplicate_in_request": sorted(dup_in_request),
                    "duplicate_in_queue": sorted(duplicate_in_queue),
                    "overlap_last_round": sorted(overlap_last_round),
                    "invalid": sorted(invalid),
                },
                "message": "没有设备可入队：全部重复、与上一轮重叠或不存在，可勾选“作为新一轮重算”后再提交",
            }

        batch = Batch(accepted)
        batches.append(batch)
        _refresh_queued_devices()
        queue_wakeup.notify_all()
        return {
            "batch_id": batch.batch_id,
            "accepted": accepted,
            "skipped": {
                "duplicate_in_request": sorted(dup_in_request),
                "duplicate_in_queue": sorted(duplicate_in_queue),
                "overlap_last_round": sorted(overlap_last_round),
                "invalid": sorted(invalid),
            },
            "message": "已加入重算队列" if not (duplicate_in_queue or overlap_last_round or dup_in_request or invalid)
                       else "部分设备已入队，重复/重叠/无效设备已跳过",
        }


@app.get("/api/health/queue")
def get_queue():
    """当前队列进度：进行中/排队中的批次、已完成台数汇总。"""
    active, finished = [], []
    totals = {"total": 0, "completed": 0, "success": 0, "failed": 0, "running": 0, "pending": 0}
    current_device = None
    with health_lock:
        for b in batches:
            d = b.to_dict()
            if d["status"] in ("queued", "running", "partial") and d["counts"]["completed"] < d["counts"]["total"]:
                active.append(d)
                if current_device is None and d["current_device_id"] is not None:
                    current_device = d["current_device_id"]
            else:
                finished.append({"batch_id": d["batch_id"], "status": d["status"], "counts": d["counts"],
                                 "created_at": d["created_at"]})
            c = d["counts"]
            if d["counts"]["completed"] < d["counts"]["total"]:
                for k in totals:
                    totals[k] += c[k]
    return {
        "active": active,
        "finished": list(reversed(finished))[-10:],
        "totals": totals,
        "current_device_id": current_device,
        "queued_device_ids": sorted(queued_devices),
    }


@app.get("/api/health/batches")
def list_batches():
    """最近批次列表（最新在前）。"""
    with health_lock:
        return {"batches": [b.to_dict() for b in reversed(batches)]}


@app.get("/api/health/batches/{batch_id}")
def get_batch(batch_id: str):
    with health_lock:
        for b in batches:
            if b.batch_id == batch_id:
                return b.to_dict()
    raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在或已过期")


@app.post("/api/health/retry")
def retry_device(req: RetryRequest):
    """单台重试：按统一口径同步重算并更新结果。
    若设备仍在其他批次队列中（pending/running），拒绝，避免同一台重复入队。"""
    dev = devices.get(req.device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"设备 #{req.device_id} 不存在")
    with health_lock:
        if req.device_id in queued_devices:
            raise HTTPException(status_code=409,
                                detail=f"设备 #{req.device_id} 正在队列中处理，暂不能重试")
        item = None
        for b in batches:
            for it in b.items:
                if it.device_id == req.device_id and (
                        req.item_id is None or it.item_id == req.item_id):
                    item = it
                    break
            if item:
                break

    # 队列外执行，不占全局唯一入队名额
    try:
        tmp = QueueItem(req.device_id)
        _run_item(tmp)
        result = tmp.result
    except Exception as e:
        message = str(e)
        if item is not None:
            with health_lock:
                item.error = message
                item.attempts += 1
        raise HTTPException(status_code=503, detail=message)

    with health_lock:
        health_results[req.device_id] = result
        if item is not None:
            item.status = "success"
            item.result = result
            item.error = None
            item.finished_at = time.time()
            item.attempts += 1
        _refresh_queued_devices()
    return {"result": result, "message": f"设备 #{req.device_id} 重算成功"}


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