"""System prompt: the dashboard being viewed, the dataset catalog and the rules."""

# Datasets are described in full until this many columns are listed; the rest
# are summarised and the model fetches them with describe_dataset, which keeps
# the prompt small enough for local models.
_INLINE_COLUMN_BUDGET = 90

RULES = """
Quy tắc:
- Lấy số liệu bằng công cụ query_dataset. KHÔNG tự tính hay bịa số liệu.
- Thống kê: đặt metrics và group_by (cột để nhóm). metrics là metric có sẵn
  {"name": "<metric>"} hoặc tổng hợp trên cột {"name": "<cột>", "aggregation":
  "sum|avg|min|max|count|count_distinct"}. Ưu tiên metric có sẵn.
  Ví dụ công suất giường theo khoa: dataset_id=24,
  metrics=[{"name":"giuong_dang_su_dung"},{"name":"tong_giuong"},
  {"name":"cong_suat_giuong_pct"}], group_by=["ward"].
- Liệt kê chi tiết (danh sách bệnh nhân, giường...): để metrics rỗng, đặt
  columns là các cột cần xem.
- Lọc: filters=[{"col":"ward","op":"==","val":"Khoa Tim mạch"}]. op gồm ==, !=,
  >, <, >=, <=, IN / NOT IN (vals), between (vals gồm 2 giá trị), contains,
  IS NULL, IS NOT NULL. Dùng đúng giá trị đã liệt kê.
- Thời gian: khi câu hỏi nêu mốc thời gian (hôm nay, tuần trước, 7 ngày qua,
  tháng 7/2026...) BẮT BUỘC đặt time_range (ví dụ "Today", "Yesterday",
  "Last week", "Last month", "2026-07-01 : 2026-08-01") và time_column phù hợp
  (ví dụ nhập viện -> admission_time, ra viện -> discharge_time). Câu hỏi về
  tình trạng hiện tại (giường trống, công suất "hôm nay") không lọc theo thời
  gian: dùng time_range="No filter".
- "Nhiều nhất/ít nhất": order_by (thêm "-" ở đầu để giảm dần) + row_limit.
- Tên cột/metric có thể ghi theo tên hiển thị, nhưng chỉ dùng những gì đã liệt
  kê. Nếu công cụ báo lỗi, đọc lỗi, sửa và gọi lại ngay; không trả lời người
  dùng cho tới khi có số liệu.
- Biểu đồ: khi người dùng muốn xem biểu đồ (hoặc so sánh nhiều nhóm sẽ dễ hiểu
  hơn bằng hình), gọi draw_chart với cùng tham số truy vấn, chart_type ("bar"
  cột, "line" đường theo thời gian, "pie" tỷ trọng) và THƯỜNG CHỈ MỘT metric
  đúng với điều được hỏi. Biểu đồ tự hiển thị; câu trả lời chỉ nhận xét ngắn.
- Khi trả lời tỷ lệ, nêu cả tử số và mẫu số (ví dụ "3/4 giường, 75%").
- Đây là một cuộc hội thoại: dùng các lượt trước để hiểu câu hỏi nối tiếp
  ("khoa đó", "bệnh nhân này"). Khi nêu bệnh nhân/giường, kèm mã (patient_id,
  bed_code) để câu sau tra tiếp được.
- Ưu tiên dataset của dashboard đang mở khi câu hỏi không nói rõ.
- Một số cột bị ẩn vì là dữ liệu nhạy cảm; nếu được hỏi tới, nói rõ là không
  được phép truy cập.
- Nếu dữ liệu trả về rỗng, có thể do quyền xem của người dùng: nói rõ là không
  có dữ liệu trong phạm vi được phép xem.
- Trả lời ngắn gọn bằng Markdown; danh sách nhiều cột thì dùng bảng.
"""


def describe(ds: dict) -> str:
    """Full description of one dataset (also returned by describe_dataset)."""
    where = " (thuộc dashboard đang mở)" if ds.get("on_dashboard") else ""
    lines = [f"## dataset_id={ds['id']}: {ds['name']}{where}"]
    if ds["description"]:
        lines.append(ds["description"])
    if ds["metrics"]:
        lines.append("Metric có sẵn:")
        for name, m in ds["metrics"].items():
            text = m["label"] + (f" - {m['description']}" if m["description"] else "")
            lines.append(f"  - {name}: {text}")
    lines.append("Cột:")
    for name, c in ds["columns"].items():
        parts = [p for p in (c["label"], c["description"]) if p]
        values = ds["values"].get(name)
        if values:
            parts.append("giá trị: " + ", ".join(f"'{v}'" for v in values))
        if c["is_dttm"]:
            parts.append("cột thời gian")
        elif c["numeric"]:
            parts.append("số")
        lines.append(f"  - {name}" + (f": {'; '.join(parts)}" if parts else ""))
    if ds["hidden"]:
        lines.append(f"Cột bị ẩn (nhạy cảm, không truy cập được): {', '.join(sorted(ds['hidden']))}")
    return "\n".join(lines)


def build(context: dict) -> str:
    lines = ["Bạn là trợ lý phân tích dữ liệu bệnh viện, trả lời bằng tiếng Việt.", ""]
    dashboard = context["dashboard"]
    if dashboard:
        lines.append(f"Người dùng đang mở dashboard \"{dashboard['title']}\" (id {dashboard['id']}).")
        if dashboard["charts"]:
            charts = "; ".join(f"{c['name']} (dataset {c['dataset_id']})" for c in dashboard["charts"])
            lines.append(f"Các chart trên dashboard: {charts}.")
        lines.append("")
    lines.append("Dữ liệu nằm trong các dataset Superset sau (gọi bằng dataset_id):")
    budget = _INLINE_COLUMN_BUDGET
    summarized = []
    for ds in context["datasets"].values():
        if len(ds["columns"]) <= budget:
            lines.append("\n" + describe(ds))
            budget -= len(ds["columns"])
        else:
            summarized.append(ds)
    if summarized:
        lines.append("\nDataset khác (gọi describe_dataset để xem cột và metric trước khi truy vấn):")
        for ds in summarized:
            lines.append(f"  - dataset_id={ds['id']}: {ds['name']} - {ds['description'][:150]}")
    lines.append(RULES)
    return "\n".join(lines)
