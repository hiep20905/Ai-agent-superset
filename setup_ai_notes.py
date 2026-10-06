"""Store usage notes for the assistant on the datasets themselves.

The notes go in the dataset's `extra` JSON under "ai_notes" (other keys are
kept), and the assistant shows them with the dataset's columns and metrics. They
can also be edited in Superset: Dataset > Edit > Settings > Extra.

Edit NOTES for your datasets, then run inside the Superset container:
    docker cp setup_ai_notes.py <container>:/tmp/
    docker exec <container> python /tmp/setup_ai_notes.py
"""

import json

from superset.app import create_app

NOTES: dict[int, list[str]] = {
    24: [
        "Ảnh chụp hiện tại, mỗi dòng một giường; chỉ chứa bệnh nhân đang nằm viện "
        "(không có lịch sử đầy đủ).",
        "Công suất, số bệnh nhân, giường trống là số hiện tại: KHÔNG lọc thời gian "
        "(giường trống không có admission_time nên sẽ bị loại).",
        "Công suất giường theo khoa: metrics giuong_dang_su_dung, tong_giuong, "
        "cong_suat_giuong_pct, group_by ward.",
        "Số ca nhập/ra viện theo kỳ hoặc xu hướng: metric encounter_id với aggregation "
        "count_distinct, time_column admission_time (nhập viện) hoặc discharge_time "
        "(ra viện). admissions_today/discharges_today chỉ là số của hôm nay.",
        "Phòng: cột room (ví dụ '201', 'ICU-01'). Số ghi chú theo phòng: lọc room, "
        "metric tong_ghi_chu.",
    ],
    30: [
        "Mỗi dòng một ghi chú lâm sàng; không có nội dung ghi chú.",
        "Ghi chú khẩn cấp: metric so_ghi_chu_khan (severity_level = URGENT), "
        "group_by ward.",
        "Không có cột phòng: lọc bed_code contains '.<phòng>.' (mã giường dạng "
        "'TM.201.01').",
        "Kết hợp với công suất giường: truy vấn dataset 24 riêng rồi ghép theo ward.",
    ],
}

app = create_app()
with app.app_context():
    from superset import db
    from superset.connectors.sqla.models import SqlaTable

    for ds_id, notes in NOTES.items():
        ds = db.session.get(SqlaTable, ds_id)
        if ds is None:
            print(f"dataset {ds_id}: not found, skipped")
            continue
        try:
            extra = json.loads(ds.extra or "{}")
        except ValueError:
            extra = {}
        extra["ai_notes"] = notes
        ds.extra = json.dumps(extra, ensure_ascii=False)
        print(f"dataset {ds_id} ({ds.table_name}): {len(notes)} notes")
    db.session.commit()
