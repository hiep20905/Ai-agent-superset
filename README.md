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
                   tool_start/result,│      describe_dataset                    │
                   token, done       │ 4. Truy vấn bằng ChartDataCommand với   │
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
| `.../query.py` | Dựng truy vấn: metric/tổng hợp, bộ lọc, thời gian, sắp xếp |
| `.../timerange.py` | Nhận diện mốc thời gian tiếng Việt và đề xuất `time_range` |
| `.../text.py` | So khớp không dấu |
| `.../tools.py` | Khai báo và thực thi các công cụ cho AI |
| `.../prompt.py` | Soạn system prompt (dashboard, dataset, quy tắc) |
| `.../charts.py` | Dựng biểu đồ và link "Mở trong Superset" |
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

Thêm nội dung của `superset_config.example.py` vào `superset_config.py`:

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `HOSPITAL_CHAT_PROVIDER` | `ollama` hoặc `gemini` | `gemini` |
| `HOSPITAL_CHAT_OLLAMA_URL` | Địa chỉ Ollama | `http://host.docker.internal:11434` |
| `HOSPITAL_CHAT_OLLAMA_MODEL` | Model Ollama | `qwen3:8b` |
| `HOSPITAL_CHAT_OLLAMA_CTX` / `_TIMEOUT` | Context (token) / thời gian chờ (giây) | `16384` / `300` |
| `GEMINI_API_KEY` | Khóa Gemini (khi dùng `gemini`) | – |
| `HOSPITAL_CHAT_MODEL` | Model Gemini chính | `gemini-3.8-flash` |
| `HOSPITAL_CHAT_FALLBACK_MODELS` | Model Gemini dự phòng khi quá tải hoặc hết hạn mức | – |
| `HOSPITAL_CHAT_DATASETS` | Id các dataset luôn được tra cứu (cộng với dataset của dashboard đang mở) | `24,30` |
| `HOSPITAL_CHAT_DASHBOARD_ONLY` | `true`: khi đang mở dashboard thì chỉ dùng dataset của dashboard đó | `false` |
| `HOSPITAL_CHAT_SENSITIVE_COLUMNS` | Cột ẩn với AI: `cột` (mọi dataset) hoặc `<dataset_id>.cột` | – |
| `HOSPITAL_CHAT_VALUE_COLUMNS` | Cột cần đưa danh sách giá trị cho AI (tên khoa...) | `ward,bed_status,...` |
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
- Trợ lý chỉ có công cụ **đọc** (`query_dataset`, `draw_chart`), không sửa hay
  xóa được dữ liệu.
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
