# Auto Model Assessment & Routing

Tự động **đánh giá** từng model (provider/model do user cấu hình), **chấm điểm phù hợp**
cho mỗi loại task, rồi **định tuyến** mỗi lượt chat/agent tới model phù hợp nhất — theo
3 chế độ **Off / Auto / Manual** bật ngay trên màn hình chat (Cowork, Co4E, AI-Edit).

Module này được xây dựng để **hoà vào đúng stack sẵn có** của Cowork-Local (PySide6
desktop app), thay vì dựng một service FastAPI riêng:

| Bản mô tả gốc (đề bài) | Hiện thực trong app này |
|---|---|
| Config YAML | Config JSON `~/.cowork_local/config.json` (chuẩn của app) + file assessment riêng |
| FastAPI REST endpoints | `RoutingService` (Python facade) — các method ánh xạ 1-1 với endpoint |
| `httpx` async + `asyncio.Semaphore` | Tái dùng `providers/` (requests) + `ThreadPoolExecutor` với **semaphore theo từng provider** |
| APScheduler | `QTimer` (giống `core/task_scheduler.py`) — không thêm dependency |
| `clients.py` (Anthropic/OpenAI) | `AppProbeClient` bọc `AppContext.build_provider_for` (đã có sẵn TLS-trust, retry 429, gateway) |

## Kiến trúc

```
core/routing/
  models.py            # Pydantic v2: ModelMetadata, ProbeResult, ModelAssessment,
                       #   SwitchDecision, PendingSwitch, TaskType/Policy/SwitchMode
  store.py             # AssessmentStore: JSON, atomic write (temp+rename), history backup
  metadata.py          # STATIC_METADATA + enrich() (dùng lại core/model_pricing cho giá)
  clients.py           # AppProbeClient (bọc Provider có sẵn) + ProbeClient protocol
  prober.py            # BENCHMARK_TASKS, probe_model(), make_judge(), semaphore/provider
  scorer.py            # compute_fit_score() + POLICY_WEIGHTS
  selector.py          # rank_models()/best_model() — tính lại fit theo policy, không probe lại
  classifier.py        # classify(prompt) -> TaskType (heuristic, fallback LLM tuỳ chọn)
  switch_controller.py # decide() (thuần) + PendingSwitchRegistry (TTL, idempotent)
  orchestrator.py      # check_and_update(): enrich -> probe -> score -> store
  service.py           # RoutingService — facade UI gọi
  scheduler.py         # RoutingScheduler (QTimer): reassess định kỳ + dọn pending hết hạn
```

## Công thức fit score

```
fit = w_quality * quality
    + w_cost    * 1/(1 + cost)
    + w_latency * 1/(1 + latency_s)
```

`POLICY_WEIGHTS` (mỗi hàng cộng = 1.0):

| Policy | quality | cost | latency |
|---|---|---|---|
| `quality` | 0.80 | 0.10 | 0.10 |
| `cost` | 0.20 | 0.70 | 0.10 |
| `latency` | 0.20 | 0.10 | 0.70 |
| `balanced` | 0.50 | 0.25 | 0.25 |

- Probe **fail** → fit = 0 (model không dùng được thì không bao giờ được chọn).
- Giá **không rõ** → để `None`, đánh dấu `metadata_incomplete=True` (không đoán bừa).

## Config (trong `config.json`, mục `routing`)

```jsonc
"routing": {
  "switch_mode": "off",            // mặc định toàn cục: "off" | "auto" | "manual"
  "policy": "balanced",            // "quality" | "cost" | "latency" | "balanced"
  "min_score_gain": 0.05,          // chỉ chuyển nếu model mới hơn model hiện tại ≥ ngưỡng này
  "confirm_timeout_sec": 60,       // (manual) hết giờ chờ confirm → giữ model hiện tại
  "reassess_interval_hours": 24,   // lịch reassess; 0 = tắt
  "per_provider_concurrency": 2,   // số probe song song tối đa mỗi provider (chống rate limit)
  "judge_provider": "",            // provider của judge ("" → active provider)
  "judge_model": "",               // model chấm điểm cố định ("" → default rẻ theo provider)
  "candidates": [                  // model muốn đánh giá; rỗng → tự lấy model đang cấu hình
    {"provider": "anthropic", "model_id": "claude-opus-4-8", "tier": "powerful"},
    {"provider": "anthropic", "model_id": "claude-haiku-4-5", "tier": "fast"}
  ],
  "auto_reassess_on_add": true,    // thêm model mới → reassess ngay
  "surface_modes": {               // toggle Off/Auto/Manual của TỪNG màn hình ("" = theo switch_mode)
    "cowork": "", "co4e": "", "ai_edit": ""
  }
}
```

Kết quả assessment **KHÔNG** nằm trong `config.json` mà ở file riêng:
`~/.cowork_local/assessments.json` (+ backup lịch sử ở `assessments_history/<timestamp>.json`).

## Toggle Off / Auto / Manual (trên màn hình chat)

Mỗi màn hình chat có một toggle nhỏ cạnh ô chọn model:

- **Off** — tắt định tuyến, luôn dùng model đang chọn.
- **Auto** — tự động chuyển sang model phù hợp nhất (nếu `gain ≥ min_score_gain`),
  chạy luôn, hiện dòng thông báo `↪ Auto-routed to …`.
- **Manual** — hiện hộp thoại xác nhận (có đếm ngược `confirm_timeout_sec`); user
  đồng ý mới chuyển, từ chối / hết giờ thì giữ model hiện tại.

Toggle được lưu **riêng cho từng màn hình** (`surface_modes`) và ghi đè `switch_mode` toàn cục.

## Logical API (RoutingService)

| Method | Tương đương REST trong đề bài |
|---|---|
| `reassess(policy=None)` / `reassess_background()` | `POST /models/reassess` |
| `best_for(task_type, policy)` | `GET /models/best` |
| `status()` / `assessments()` | `GET /models/assessments` |
| `add_candidate(provider, model_id, tier)` | `POST /models/add` (tự trigger reassess) |
| `route(surface, prompt, provider, model)` | phần quyết định của `POST /task/execute` |
| `create_pending()` / `resolve_pending(id, approve, run)` | `POST /task/confirm-switch` (idempotent) |
| `get_routing_config()` / `update_routing_config(**)` | `GET`/`PATCH /routing/config` |

## Bảo mật & chi phí

- **API key** đọc từ env (qua `api_key_env` của provider) — không ghi key vào config/log.
- **Probe tốn tiền** → chỉ chạy theo lịch / khi thêm model / khi bấm "Reassess now".
  Mỗi lần reassess ghi log số lượng API call.
- **Idempotent** — reassess ổn định (chỉ latency dao động ~µs, dưới xa `min_score_gain`);
  ghi atomic nên ngắt giữa chừng không hỏng config.
- **Không gọi API thật trong test** — `clients.py`/`judge()` được mock hoàn toàn.

## Chạy test

```bash
# từ thư mục cha của package (…/cowork_local_20260722)
python -m pytest cowork_local/tests/routing/ -q
```

Bao phủ: `scorer`, `store` (atomic + history), `selector`, `switch_controller`
(Auto/Manual/Off, timeout, idempotent), `orchestrator` (mock client), `classifier`,
và `service` (end-to-end reassess → route → confirm).
