"""离线逻辑测试：stub 掉 fastapi/numpy，直接驱动 main 里的排队重算流程。"""
import sys, types, time, threading, importlib

# --- stub fastapi ---
fastapi = types.ModuleType("fastapi")
routes = {}
class HTTPException(Exception):
    def __init__(self, status_code, detail): self.status_code = status_code; self.detail = detail
class FastAPI:
    def __init__(self, **kw): self.middleware = None
    def add_middleware(self, *a, **kw): pass
    def on_event(self, name):
        def deco(fn): return fn
        return deco
    def post(self, path):
        def deco(fn): routes[("POST", path)] = fn; return fn
        return deco
    def get(self, path):
        def deco(fn): routes[("GET", path)] = fn; return fn
        return deco
    def websocket(self, path):
        def deco(fn): return fn
        return deco
class _BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)
fastapi.FastAPI = FastAPI
fastapi.HTTPException = HTTPException
fastapi.WebSocket = object
fastapi.WebSocketDisconnect = Exception
fastapi.BaseModel = _BaseModel
mw = types.ModuleType("fastapi.middleware"); cors = types.ModuleType("fastapi.middleware.cors")
cors.CORSMiddleware = object; mw.cors = cors
pydantic = types.ModuleType("pydantic"); pydantic.BaseModel = _BaseModel
np = types.ModuleType("numpy")
np.mean = lambda xs: sum(xs) / len(xs)
sys.modules.update({"fastapi": fastapi, "fastapi.middleware": mw,
                    "fastapi.middleware.cors": cors, "pydantic": pydantic, "numpy": np})

sys.path.insert(0, "/workspace/backend")
import app.main as m

# 加快测试：把每台重算耗时压到接近 0
m.SIMULATED_LATENCY = (0.01, 0.03)
threading.Thread(target=m.recalc_worker, daemon=True).start()

def wait_round(rid, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = m.recalc_status()
        if not any(r["id"] == rid for r in st["active_rounds"]):
            return next(r for r in st["history"] if r["id"] == rid)
        time.sleep(0.02)
    raise AssertionError(f"round {rid} timed out")

# ---- 1) 首轮：含重复 id（第二个 3 重复）、不存在 id（99），有效 1,2,6,3 ----
resp = m.enqueue_recalc(m.RecalcEnqueueRequest(device_ids=[1, 2, 6, 3, 3, 99]))
assert resp["round_id"] == 1, resp
assert resp["accepted"] == [1, 2, 6, 3], resp
reasons = {s["device_id"]: s["reason"] for s in resp["skipped"]}
assert reasons[3].startswith("本次请求内重复入队"), reasons
assert reasons[99] == "设备不存在，已跳过"
print("enqueue#1 skips:", resp["skipped"])

r1 = wait_round(1)
assert r1["completed"] == 4 and r1["succeeded"] == 3 and r1["failed"] == 1, r1
job6 = next(j for j in r1["jobs"] if j["device_id"] == 6)
assert job6["status"] == "FAILED" and "超时" in job6["error"]
print("round1 done:", r1["succeeded"], "ok /", r1["failed"], "failed")

# 每台成功的结果都要带评分/在线率/拖低项/版本
for j in r1["jobs"]:
    if j["status"] == "SUCCESS":
        res = j["result"]
        assert 0 <= res["health_score"] <= 100
        assert res["formula_version"] == "v1.0"
        assert isinstance(res["top_deductions"], list) and len(res["top_deductions"]) <= 3
        assert abs(res["online_rate"] - round(min(1.0, res["snapshot"]["uptime"] /
               max(1, res["snapshot"]["uptime"] + res["snapshot"]["fault_count"])) * 100, 1)) < 1e-9
print("per-device results carry score/online_rate/top_deductions/version")

# 快照定格：入队时的快照重算必须与 evaluate_health 完全一致（口径一致，忽略时间戳）
j1 = next(j for j in r1["jobs"] if j["device_id"] == 1)
again = m.evaluate_health(j1["result"]["snapshot"])
assert {k: v for k, v in j1["result"].items() if k != "computed_at"} == \
       {k: v for k, v in again.items() if k != "computed_at"}
print("snapshot re-eval identical (same formula version)")

# ---- 2) 进度推进可见：趁队列还在跑时读 status ----
big = m.enqueue_recalc(m.RecalcEnqueueRequest(device_ids=list(range(4, 6)) + [7, 8, 9]))
rid = big["round_id"]
saw_progress = False
deadline = time.time() + 5
while time.time() < deadline:
    st = m.recalc_status()
    ar = next((r for r in st["active_rounds"] if r["id"] == rid), None)
    if ar and (ar["current_device_id"] is not None or 0 < ar["completed"] < ar["total"]):
        saw_progress = True
        print("progress observed:", ar["completed"], "/", ar["total"],
              "current=#%s" % ar["current_device_id"])
        break
    time.sleep(0.005)
assert saw_progress, "队列进度从未被观察到"
r2 = wait_round(rid)
assert r2["succeeded"] == r2["total"] == 5, r2

# ---- 3) 与上一轮范围重叠：设备 4,5 在上一轮 -> 跳过；6 是更早轮次且当前 FAILED
#        （不在队列也不在运行中）-> 允许重新入队；10 为新设备 -> 入队
resp3 = m.enqueue_recalc(m.RecalcEnqueueRequest(device_ids=[4, 5, 6, 10]))
assert resp3["accepted"] == [6, 10], resp3
sk = {s["device_id"]: s["reason"] for s in resp3["skipped"]}
assert all("与上一轮重算范围重叠" in sk[d] for d in (4, 5)), sk
print("overlap skips:", resp3["skipped"])
r3 = wait_round(resp3["round_id"])
# 6 在新轮次里首算依旧失败，10 成功
assert r3["succeeded"] == 1 and r3["failed"] == 1, r3
new6 = next(j for j in r3["jobs"] if j["device_id"] == 6)
assert new6["attempt"] == 1 and new6["status"] == "FAILED"

# ---- 4) 同一台设备不能重复入队（并发两个请求各带 11,12，交错时也不能重）----
results = []
def fire(ids):
    results.append(m.enqueue_recalc(m.RecalcEnqueueRequest(device_ids=ids)))
t_a = threading.Thread(target=fire, args=([11, 12],))
t_b = threading.Thread(target=fire, args=([11, 12],))
t_a.start(); t_b.start(); t_a.join(); t_b.join()
acc = sorted(d for r in results for d in r["accepted"])
skp = [s for r in results for s in r["skipped"]]
assert acc == [11, 12], acc
dup = [s for s in skp if "已在当前重算队列中" in s["reason"]]
assert {s["device_id"] for s in dup} == {11, 12}, skp
print("concurrent same-device enqueue guarded:", [s["reason"] for s in dup])
for rid in {r["round_id"] for r in results if r["round_id"]}:
    wait_round(rid)

# ---- 5) 单台重试：6 首算失败 -> 重试成功；重试沿用同一快照同版本 ----
m.last_round_device_ids  # noqa
try:
    m.retry_recalc(m.RetryRequest(round_id=1, device_id=1))
    raise AssertionError("成功的设备不应允许重试")
except m.HTTPException as e:
    assert e.status_code == 409
try:
    m.retry_recalc(m.RetryRequest(round_id=999, device_id=6))
    raise AssertionError("不存在的记录应 404")
except m.HTTPException as e:
    assert e.status_code == 404

rt = m.retry_recalc(m.RetryRequest(round_id=1, device_id=6))
assert rt["attempt"] == 2 and rt["formula_version"] == "v1.0"
deadline = time.time() + 5
while time.time() < deadline:
    st = m.recalc_status()
    h = next(r for r in st["history"] if r["id"] == 1)
    j6 = next(j for j in h["jobs"] if j["device_id"] == 6)
    if j6["status"] == "SUCCESS":
        print("retry succeeded: attempt", j6["attempt"], "score", j6["result"]["health_score"],
              "top:", [d["label"] for d in j6["result"]["top_deductions"]])
        break
    if j6["status"] == "FAILED":
        raise AssertionError("retry should succeed")
    time.sleep(0.02)
else:
    raise AssertionError("retry did not finish")

# 重试期间历史轮次状态应短暂变为 RETRYING（状态接口自检）
st = m.recalc_status()
assert st["formula_version"] == "v1.0" and "queue_size" in st
h1 = next(r for r in st["history"] if r["id"] == 1)
assert h1["succeeded"] == 4 and h1["failed"] == 0 and h1["status"] == "DONE", h1
print("history after retry: round1 ->", h1["succeeded"], "ok,", h1["failed"], "failed")

# ---- 6) 重试重复提交防护：FAILED 的设备已存在一条 QUEUED 记录时应拒绝 ----
j6rec = m.recalc_jobs[(1, 6)]
assert j6rec.status == "SUCCESS"  # 上一步已重试成功
j6rec.status = "FAILED"           # 伪造再次失败
j6rec.status = "QUEUED"           # 且已有一条在队列中
try:
    m.retry_recalc(m.RetryRequest(round_id=1, device_id=6))
    raise AssertionError("已在队列中的重试应被 409 拒绝")
except m.HTTPException as exc:
    assert exc.status_code == 409 and "重复" in exc.detail
    print("duplicate retry submission rejected:", exc.detail)

print("\nALL BACKEND QUEUE TESTS PASSED")
