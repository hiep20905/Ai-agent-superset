# Hospital AI Agent — Superset extension

Trợ lý AI nhúng vào Apache Superset. Người dùng hỏi bằng tiếng Việt về giường
bệnh, bệnh nhân, đợt điều trị, thống kê ghi chú lâm sàng; trợ lý tự truy vấn
**dataset Superset**, trả lời bằng Markdown (bảng, chữ đậm) và **vẽ biểu đồ**
ngay trong khung chat.

- **Đúng quyền**: mọi truy vấn chạy bằng tài khoản người đang hỏi, qua đúng
  đường dữ liệu của dashboard, nên quyền dataset và **Row Level Security** được
  áp dụng tự động.
- **AI nội bộ hoặc đám mây**: chạy với [Ollama](https://ollama.com) (dữ liệu
  không ra khỏi mạng nội bộ) hoặc Google Gemini API, đổi bằng một dòng cấu hình.
- **Nhớ hội thoại**: hiểu câu hỏi nối tiếp ("khoa đó", "bệnh nhân này"), lưu
  theo từng người dùng trong cache Redis của Superset.
- **Biểu đồ từ số liệu thật**: cột / đường / tròn, dựng từ kết quả truy vấn (AI
  không tự đưa số), kèm nút "Mở trong Superset" để chỉnh sửa và lưu.
- **Theo ngữ cảnh dashboard**: tự dùng dataset của dashboard đang mở (kiểm tra
  quyền xem dashboard), cộng thêm các dataset cấu hình sẵn.
- **Trả lời dạng streaming**: chữ hiện dần, kèm tiến trình "Đang truy vấn dữ
  liệu…", "Đã lấy 5 dòng dữ liệu".
- **Truy vấn linh hoạt**: metric có sẵn hoặc sum/avg/min/max/count trên cột;
  lọc ==, IN, between, contains...; tên cột/giá trị gõ không dấu vẫn khớp
  ("khoa tim mach" -> 'Khoa Tim mạch'); câu hỏi có mốc thời gian bắt buộc lọc
  theo thời gian.
- **Ẩn cột nhạy cảm**: cột được đánh dấu không bao giờ tới AI.

## Kiến trúc

```
 Trình duyệt                          Superset (backend extension)
 ┌──────────────┐ POST /ask (CSRF)   ┌─────────────────────────────────────────┐
 │ ChatPanel    │ ─────────────────▶ │ 1. Ngữ cảnh: dashboard đang mở + dataset │
 │ (React)      │  câu hỏi, id hội   │    người dùng được xem (cột, metric, mô  │
 │              │  thoại, dashboard  │    tả, giá trị; bỏ cột nhạy cảm)         │
 │ Markdown     │                    │ 2. Lịch sử hội thoại (Redis)            │
 │ Chart (SVG)  │ ◀───────────────── │ 3. Vòng gọi AI + công cụ:                │
 └──────────────┘  SSE: context,     │      query_dataset, draw_chart,         │
                   tool_start/result,│      describe_dataset, search_datasets,  │
                   token, done       │      get_column_values, get_chart_catalog│
                                     │ 4. Truy vấn bằng ChartDataCommand với   │
                                     │    tài khoản người hỏi (quyền + RLS)    │
                                     └────────────────┬────────────────────────┘
                                                      │
                                     Ollama (nội bộ)  hoặc  Gemini API (Google)
```

## Cấu trúc mã nguồn

| Đường dẫn | Vai trò |
|---|---|
| `extension.json` | Tên, phiên bản, publisher của extension |
| `backend/src/demo/hospital_chat/api.py` | Endpoint `POST /ask`: kiểm tra CSRF, trả luồng SSE |
| `.../config.py` | Đọc toàn bộ biến cấu hình `HOSPITAL_CHAT_*` |
| `.../catalog.py` | Ngữ cảnh dashboard, danh mục dataset theo người dùng, ẩn cột nhạy cảm, khớp tên |
| `.../data.py` | Chạy truy vấn qua `ChartDataCommand` (quyền + RLS) |
| `.../query.py` | Dựng truy vấn: metric/tổng hợp, bộ lọc, thời gian, gộp theo kỳ (`time_grain`), sắp xếp |
| `.../timerange.py` | Mốc thời gian theo lịch (hôm nay, tuần trước...) dạng `YYYY-MM-DD : YYYY-MM-DD` đưa vào prompt; nhận diện mốc thời gian tiếng Việt; sửa `Today`/`Yesterday` (Superset hiểu sai) |
| `.../text.py` | So khớp không dấu |
| `.../tools.py` | Khai báo và thực thi các công cụ cho AI |
| `.../prompt.py` | Soạn system prompt (dashboard, dataset, ghi chú nghiệp vụ, phân loại câu hỏi, quy tắc dùng công cụ) |
| `.../charts.py` | Dựng biểu đồ và link "Mở trong Superset" |
| `.../chartquery.py` | Truy vấn của từng chart trên dashboard (từ `query_context` đã lưu, hoặc dựng lại từ `form_data`), thu hẹp bằng bộ lọc/kỳ thời gian |
| `.../questionlog.py` | Ghi từng câu hỏi, các lần gọi công cụ và câu trả lời ra file JSONL (`HOSPITAL_CHAT_LOG_FILE`) |
| `setup_ai_notes.py` | Ghi ghi chú cho AI (`ai_notes`) vào `extra` của từng dataset |
| `eval/run_eval.py`, `eval/cases.hospital.json` | Bộ kiểm thử hồi quy với dữ liệu và model thật |
| `.../providers.py` | Vòng gọi Ollama / Gemini dạng streaming, model dự phòng |
| `.../history.py` | Nhớ hội thoại trong cache Redis |
| `.../entrypoint.py` | Superset nạp file này khi khởi động để đăng ký API |
| `frontend/src/index.tsx` | Đăng ký khung chat với Superset (`chat.registerChat`) |
| `frontend/src/api.ts` | Gọi `POST /ask` kèm CSRF token, đọc luồng SSE, nhận biết dashboard đang mở |
| `frontend/src/ChatTrigger.tsx` | Nút bong bóng 💬/✕, bật/tắt khung chat |
| `frontend/src/ChatPanel.tsx` | Khung chat: tin nhắn streaming, tiến trình xử lý, nút "Cuộc trò chuyện mới" |
| `frontend/src/store.ts` | Trạng thái hội thoại phía trình duyệt (giữ khi đóng/mở lại khung chat) |
| `frontend/src/Markdown.tsx` | Hiển thị Markdown an toàn (không dùng innerHTML) |
| `frontend/src/Chart.tsx` | Biểu đồ cột / đường / tròn bằng SVG |
| `setup_datasets.py` | Tạo/cập nhật metric và mô tả tiếng Việt cho dataset |
| `pack.py` | Đóng gói thành file `.supx` |
| `superset_config.example.py` | Mẫu cấu hình cần thêm vào `superset_config.py` |

## Yêu cầu

- Superset có hỗ trợ extension và contribution `chat`, với feature flag
  `ENABLE_EXTENSIONS` bật và `EXTENSIONS_PATH` trỏ tới thư mục chứa `.supx`.
- Node.js 18+ và npm để build frontend.
- Python 3.10+ để chạy `pack.py`.
- Một trong hai:
  - **Ollama** với model hỗ trợ tool calling (ví dụ `qwen3:8b`, `qwen3:14b`, `qwen3:32b`);
  - **Gemini API key** (gói `google-genai` có trong môi trường Superset).

## Cài đặt

### 1. Chuẩn bị dataset

Trợ lý đọc các dataset của dashboard đang mở cộng với các dataset trong
`HOSPITAL_CHAT_DATASETS` (chỉ những dataset người dùng có quyền xem). Nó dùng
**metric** và **mô tả cột** của dataset để hiểu dữ liệu, nên dataset càng được
mô tả kỹ thì AI trả lời càng đúng.

`setup_datasets.py` cấu hình 2 dataset cho dữ liệu mẫu:

- dataset giường bệnh nội trú (id 24): thêm metric công suất giường, giường
  trống, y lệnh chưa thực hiện, hồ sơ chưa ký, số ghi chú... và mô tả cột;
- dataset **"Thống kê ghi chú lâm sàng"**: tạo mới, **không chứa nội dung ghi
  chú**, chỉ dùng để đếm và thống kê.

Sửa id dataset, database id và câu SQL trong script cho khớp hệ thống của bạn,
rồi chạy trong container Superset:

```bash
docker cp setup_datasets.py <superset-container>:/tmp/
docker exec -w / <superset-container> python /tmp/setup_datasets.py
```

### 2. Cấu hình

Mã nguồn không gắn với lĩnh vực nào (bệnh viện chỉ là cấu hình). Kiến thức được
tách thành 3 lớp, đổi dữ liệu hay lĩnh vực không cần sửa mã:

| Lớp | Ở đâu | Ví dụ |
|---|---|---|
| Vai trò | `HOSPITAL_CHAT_ROLE` | "Bạn là trợ lý phân tích dữ liệu bệnh viện..." |
| Ghi chú chung | `HOSPITAL_CHAT_DOMAIN_NOTES` | Thuật ngữ dùng cho mọi dataset |
| Theo dataset | Superset: mô tả dataset/cột/metric, và `{"ai_notes": [...]}` trong `extra` của dataset (Dataset > Edit > Settings > Extra, hoặc `setup_ai_notes.py`) | "Công suất là số hiện tại, không lọc thời gian" |

Giá trị của các cột văn bản ít giá trị (tên khoa, trạng thái...) được tự động
liệt kê cho AI để lọc đúng tên (`HOSPITAL_CHAT_AUTO_VALUES_MAX`); cột trông như mã
định danh (gần mỗi dòng một giá trị) bị bỏ qua. Cột khác thì AI tra bằng
`get_column_values`. Một cột có thể bật/tắt bằng `{"ai_values": true/false}`
trong `extra` của cột. Trợ lý phân loại
câu hỏi: hỏi số liệu (truy vấn), hỏi về dữ liệu/dashboard (metadata), hỏi kiến
thức (trả lời chung, ghi rõ không lấy từ dữ liệu) và ngoài phạm vi (từ chối).

Thêm nội dung của `superset_config.example.py` vào `superset_config.py`:

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `HOSPITAL_CHAT_PROVIDER` | `ollama` hoặc `gemini` | `gemini` |
| `HOSPITAL_CHAT_OLLAMA_URL` | Địa chỉ Ollama | `http://host.docker.internal:11434` |
| `HOSPITAL_CHAT_OLLAMA_MODEL` | Model Ollama | `qwen3:8b` |
| `HOSPITAL_CHAT_OLLAMA_CTX` / `_TIMEOUT` | Context (token) / thời gian chờ (giây) | `16384` / `300` |
| `GEMINI_API_KEY` | Khóa Gemini (khi dùng `gemini`) | – |
| `HOSPITAL_CHAT_MODEL` | Model Gemini chính | `gemini-3.8-flash` |
| `HOSPITAL_CHAT_FALLBACK_MODELS` | Model Gemini dự phòng, dùng lần lượt khi model chính hết hạn mức (429, bỏ qua 1 giờ) hoặc quá tải (503, bỏ qua 5 phút) | – |
| `HOSPITAL_CHAT_GEMINI_THINKING` | Mức suy nghĩ của Gemini: `minimal`, `low`, `medium`, `high`; để trống = mặc định của model. Càng cao càng chậm (đo: `low` 1,5 giây so với mặc định 4,7 giây cho một lần gọi công cụ) | `low` |
| `HOSPITAL_CHAT_ROLE` | Câu mở đầu prompt: trợ lý là ai | trợ lý phân tích dữ liệu trên Superset |
| `HOSPITAL_CHAT_DATASETS` | Id các dataset luôn được tra cứu (cộng với dataset của dashboard đang mở) | – |
| `HOSPITAL_CHAT_DASHBOARD_ONLY` | `true`: khi đang mở dashboard thì chỉ dùng dataset của dashboard đó | `false` |
| `HOSPITAL_CHAT_SEARCH_ALL` | `true`: AI được tìm (`search_datasets`) và truy vấn mọi dataset khác mà người dùng có quyền xem. Tắt khi `DASHBOARD_ONLY` áp dụng | `true` |
| `HOSPITAL_CHAT_DOMAIN_NOTES` | Ghi chú nghiệp vụ đưa vào prompt (cách tính chỉ số, viết tắt, dataset nào dùng cho câu hỏi nào) | – |
| `HOSPITAL_CHAT_DOMAIN_NOTES_FILE` | Như trên, đọc từ file văn bản UTF-8 | – |
| `HOSPITAL_CHAT_SENSITIVE_COLUMNS` | Cột ẩn với AI: `cột` (mọi dataset) hoặc `<dataset_id>.cột` | – |
| `HOSPITAL_CHAT_AUTO_VALUES_MAX` | Cột văn bản có tối đa chừng này giá trị khác nhau thì được liệt kê cho AI (0 = tắt) | `30` |
| `HOSPITAL_CHAT_VALUE_COLUMNS` | Cột luôn được liệt kê giá trị | – |
| `HOSPITAL_CHAT_LOG_FILE` | File JSONL ghi câu hỏi, công cụ và câu trả lời. Chứa kết quả truy vấn: bảo vệ như chính dữ liệu | tắt |
| `HOSPITAL_CHAT_MAX_ROWS` | Số dòng tối đa mỗi truy vấn | `200` |
| `HOSPITAL_CHAT_HISTORY_TURNS` | Số lượt hỏi–đáp được nhớ | `8` |

> Không commit API key. Hãy đặt `GEMINI_API_KEY` bằng biến môi trường
> (ví dụ trong `docker/.env-local`).

### 3. Build và đóng gói

```powershell
cd frontend
npm install        # lần đầu
npm run build
cd ..
python pack.py     # tạo hospital-chat-0.1.0.supx
```

`pack.py` thay cho `superset-extensions build/bundle`, vì CLI này không tìm
thấy `npm` trên Windows.

### 4. Cài vào Superset

1. Chép `hospital-chat-0.1.0.supx` vào thư mục `EXTENSIONS_PATH` (ví dụ
   `docker/extensions/`).
2. Khởi động lại Superset.
3. Cấp quyền **`can read on hospital_chat_api`** cho các role được dùng trợ lý,
   và quyền xem các dataset trong `HOSPITAL_CHAT_DATASETS`.

## Bảo mật

- Truy vấn chạy bằng tài khoản người đang hỏi: người dùng không có quyền xem
  một dataset thì trợ lý cũng không thấy dataset đó, và RLS giới hạn dòng dữ
  liệu như trên dashboard.
- Trợ lý chỉ có công cụ **đọc** (`query_dataset`, `draw_chart`, `describe_dataset`,
  `search_datasets`, `get_chart_catalog`), không sửa hay xóa được dữ liệu.
- `search_datasets` chỉ trả về dataset người dùng có quyền xem; bỏ qua cột nhạy
  cảm khi so khớp. Chart trên dashboard chỉ được mô tả chi tiết khi người dùng
  truy vấn được dataset của chart, và không nêu tên cột bị ẩn.
- Mô tả dataset/cột/chart và ghi chú nghiệp vụ được coi là dữ liệu, không phải
  chỉ thị (prompt nói rõ điều này) để hạn chế prompt injection qua metadata.
- Cột nhạy cảm được ẩn hoàn toàn với AI: không có trong mô tả gửi cho AI, không
  chọn, lọc hay nhóm được. Đánh dấu bằng `HOSPITAL_CHAT_SENSITIVE_COLUMNS` hoặc
  thêm `{"ai_sensitive": true}` (hoặc `{"ai_queryable": false}`) vào trường
  `extra` của cột trong Superset. Dữ liệu rất nhạy cảm vẫn nên loại khỏi dataset,
  như dataset ghi chú lâm sàng không có cột nội dung.
- Endpoint `/ask` dùng `POST` (câu hỏi không nằm trên URL hay log truy cập) và
  tự kiểm tra CSRF token, vì REST API của FAB được miễn kiểm tra CSRF mặc định.
- Khi mở dashboard, quyền xem dashboard được kiểm tra trước khi dùng dataset của nó.
- Khi dùng Gemini, kết quả truy vấn (có thể gồm tên và chẩn đoán của bệnh nhân)
  được gửi tới Google. Dùng Ollama nội bộ nếu dữ liệu không được phép ra ngoài.

## Câu hỏi mẫu

- Công suất giường của từng khoa?
- Khoa Tim mạch có những bệnh nhân nào? → *(nối tiếp)* Bệnh nhân lớn tuổi nhất ở khoa đó là ai?
- Bệnh nhân nào có nhiều y lệnh thuốc chưa thực hiện nhất?
- Vẽ biểu đồ công suất giường theo khoa
- Vẽ biểu đồ tròn số ghi chú lâm sàng theo mức độ

## Hiệu năng

| Phần cứng | Model | Thời gian mỗi câu |
|---|---|---|
| Laptop chỉ có CPU (i7, RAM 32 GB) | qwen3:8b | 45 giây – 3 phút |
| GPU NVIDIA 16 GB | qwen3:14b | khoảng 5–10 giây |
| GPU NVIDIA 24–32 GB | qwen3:32b | khoảng 3–8 giây |
| Gemini API | gemini-3.x-flash | khoảng 3–10 giây |

## Giấy phép

Apache-2.0

## Trả lời theo dashboard đang mở

Mở dashboard nào thì trợ lý lấy ngữ cảnh của dashboard đó, không cần cấu hình riêng:

- Prompt liệt kê các chart của dashboard (đo chỉ số gì, theo chiều nào, lọc gì,
  kỳ nào) và mô tả đầy đủ các dataset của nó; dataset khác chỉ được nêu tên.
- `query_chart(chart_id, filters?, time_range?)` chạy lại đúng truy vấn của chart
  nên số liệu khớp với dashboard, kể cả chỉ số chỉ định nghĩa trong chart (metric
  SQL của plugin tự viết). Chart lưu `query_context` thì dùng nguyên truy vấn đó;
  chart cũ thì dựng lại từ `form_data`.
- Chỉ số của chart dùng được trong `query_dataset` theo tên, để nhóm/lọc khác
  chart. Cùng một tên nhưng công thức khác nhau ở hai chart thì được tách thành
  `tên [chart]`.
- Phần cố định của prompt đứng trước phần ngữ cảnh để nhà cung cấp AI dùng lại
  bộ nhớ đệm giữa các câu hỏi và dashboard.

`eval/cases.dashboards.json` kiểm thử trên các dashboard khác (hồ sơ & y lệnh,
Sales, Video Game Sales).

## Kiểm thử hồi quy

`eval/cases.hospital.json` chứa các câu hỏi mẫu theo nhóm (số liệu, thời gian, hội
thoại, metadata, kiến thức, nhiều dataset, ngoài phạm vi, mơ hồ). Mỗi câu kiểm tra
công cụ được gọi, tham số thực tế sau khi máy chủ xử lý (dataset, metric, bộ lọc,
`time_range`, `time_grain`, số dòng) và nội dung câu trả lời. Mốc thời gian viết
dạng `{this_year}`, `{last_week}`... nên không phụ thuộc ngày chạy.

```bash
docker cp eval <superset-container>:/tmp/eval
docker exec <superset-container> python /tmp/eval/run_eval.py /tmp/eval/cases.hospital.json \
    --dashboard 13 --report /tmp/eval/report.json
# chỉ chạy một số câu: --only trend_month,care_level_1   hoặc   --category "Thời gian"
```

Chạy lại sau mỗi lần sửa prompt, công cụ, ghi chú hay đổi model. Câu hỏi thật của
người dùng (trong `HOSPITAL_CHAT_LOG_FILE`) mà trợ lý trả lời sai nên được thêm
vào bộ kiểm thử.
