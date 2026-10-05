"""Idempotent: add business metrics + Vietnamese descriptions to the bed-board
dataset (id 24) and create a clinical-note statistics dataset without note text."""
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable, SqlMetric, TableColumn

    OCC = "SUM(CASE WHEN bed_status = 'occupied' THEN 1 ELSE 0 END)"
    BED_METRICS = [
        ("tong_giuong", "Tổng số giường", "COUNT(*)", "Tổng số giường (mọi trạng thái)."),
        ("giuong_dang_su_dung", "Giường đang sử dụng", OCC, "Số giường có bed_status = 'occupied'."),
        ("giuong_trong", "Giường trống", "SUM(CASE WHEN bed_status = 'empty' THEN 1 ELSE 0 END)", "Số giường có bed_status = 'empty'."),
        ("giuong_dat_truoc", "Giường đã đặt trước", "SUM(CASE WHEN bed_status = 'reserved' THEN 1 ELSE 0 END)", "Số giường có bed_status = 'reserved'."),
        ("cong_suat_giuong_pct", "Công suất giường (%)", f"ROUND(100.0 * {OCC} / NULLIF(COUNT(*), 0), 1)", "Công suất giường = giường đang sử dụng / tổng số giường x 100."),
        ("so_benh_nhan", "Số bệnh nhân", "COUNT(DISTINCT patient_id)", "Số bệnh nhân khác nhau đang nằm giường."),
        ("tong_ylenh_thuoc_chua_th", "Y lệnh thuốc chưa thực hiện", "SUM(medication_pending)", "Tổng số y lệnh thuốc chưa thực hiện."),
        ("tong_ylenh_cls_chua_th", "Y lệnh CLS chưa thực hiện", "SUM(lab_pending)", "Tổng số y lệnh cận lâm sàng chưa thực hiện."),
        ("tong_hoso_chua_ky", "Hồ sơ chưa ký", "SUM(unsigned_docs)", "Tổng số hồ sơ chưa ký."),
        ("tong_ghi_chu", "Số ghi chú lâm sàng", "SUM(note_count)", "Tổng số ghi chú lâm sàng của các đợt điều trị."),
        ("tuoi_trung_binh", "Tuổi trung bình", "ROUND(AVG(age), 1)", "Tuổi trung bình của bệnh nhân."),
        ("so_ngay_nam_vien_tb", "Số ngày nằm viện trung bình", "ROUND(AVG(EXTRACT(EPOCH FROM (COALESCE(discharge_time, NOW()) - admission_time)) / 86400)::numeric, 1)", "Số ngày nằm viện trung bình tính tới hiện tại (hoặc ngày ra viện)."),
    ]
    BED_COLUMNS = {
        "id": ("Mã dòng", "Khóa kỹ thuật của giường."),
        "ward": ("Khoa", "Tên khoa, ví dụ 'Khoa Tim mạch'."),
        "room": ("Phòng", "Số/mã phòng."),
        "bed_code": ("Mã giường", "Mã giường bệnh."),
        "bed_status": ("Trạng thái giường", "'occupied' = đang sử dụng, 'empty' = trống, 'reserved' = đã đặt trước."),
        "encounter_id": ("Mã đợt điều trị", "Mã đợt điều trị nội trú."),
        "patient_id": ("Mã bệnh nhân", "Mã bệnh nhân, ví dụ 'BN-0001'."),
        "patient_name": ("Họ tên bệnh nhân", None),
        "age": ("Tuổi", None),
        "gender": ("Giới tính", "'Nam' hoặc 'Nữ'."),
        "admission_time": ("Thời điểm nhập viện", None),
        "discharge_time": ("Thời điểm ra viện", "NULL = đang nằm viện."),
        "encounter_status": ("Trạng thái đợt điều trị", "Ví dụ 'ACTIVE'."),
        "diagnosis_code": ("Mã ICD-10", None),
        "diagnosis": ("Chẩn đoán chính", "Ví dụ 'Suy tim', 'Viêm phổi'."),
        "care_level": ("Cấp chăm sóc", "'I', 'II' hoặc 'III'."),
        "doctor": ("Bác sĩ phụ trách", None),
        "nurse": ("Điều dưỡng phụ trách", None),
        "medication_pending": ("Số y lệnh thuốc chưa thực hiện", "Của giường/bệnh nhân này."),
        "lab_pending": ("Số y lệnh CLS chưa thực hiện", "Của giường/bệnh nhân này."),
        "unsigned_docs": ("Số hồ sơ chưa ký", "Của giường/bệnh nhân này."),
        "note_count": ("Số ghi chú lâm sàng", "Số ghi chú của đợt điều trị."),
        "latest_note_at": ("Ghi chú gần nhất lúc", None),
        "admissions_today": ("Nhập viện hôm nay (toàn viện)", "Giống nhau trên mọi dòng; không cộng dồn."),
        "discharges_today": ("Ra viện hôm nay (toàn viện)", "Giống nhau trên mọi dòng; không cộng dồn."),
        "special_order_count": ("Số y lệnh đặc biệt", "Hiện luôn bằng 0."),
    }

    def apply(ds, metrics, columns, dttm=None):
        existing = {m.metric_name: m for m in ds.metrics}
        for name, verbose, expr, desc in metrics:
            m = existing.get(name) or SqlMetric(metric_name=name, table=ds)
            m.verbose_name, m.expression, m.description = verbose, expr, desc
            if name not in existing:
                ds.metrics.append(m)
        for col in ds.columns:
            if col.column_name in columns:
                col.verbose_name, col.description = columns[col.column_name]
            if dttm and col.column_name in dttm:
                col.is_dttm = True
        if dttm:
            ds.main_dttm_col = dttm[0]

    bed = db.session.get(SqlaTable, 24)
    bed.description = ("Giường bệnh nội trú hiện tại: mỗi dòng là một giường, kèm bệnh nhân, "
                       "đợt điều trị, bác sĩ, y lệnh chưa thực hiện và số ghi chú lâm sàng.")
    apply(bed, BED_METRICS, BED_COLUMNS, dttm=["admission_time", "discharge_time", "latest_note_at"])

    NOTE_NAME = "Thống kê ghi chú lâm sàng"
    NOTE_SQL = """SELECT
    n.note_id::text AS note_id,
    n.encounter_id,
    n.author_user_id,
    n.note_type,
    n.severity_level,
    n.created_at,
    e.patient_id,
    b.ward,
    b.bed_code,
    b.doctor
FROM notes_module.clinical_note AS n
LEFT JOIN warehouse.encounter AS e ON e.encounter_id = n.encounter_id
LEFT JOIN warehouse.current_bed AS b ON b.encounter_id = n.encounter_id"""
    note = db.session.query(SqlaTable).filter_by(table_name=NOTE_NAME, database_id=3).one_or_none()
    if note is None:
        note = SqlaTable(table_name=NOTE_NAME, database_id=3, sql=NOTE_SQL)
        db.session.add(note)
        db.session.flush()
    else:
        note.sql = NOTE_SQL
    note.fetch_metadata()
    note.description = ("Ghi chú lâm sàng để đếm/thống kê (theo loại, mức độ, thời gian, khoa, bác sĩ). "
                        "KHÔNG chứa nội dung ghi chú.")
    NOTE_COLUMNS = {
        "note_id": ("Mã ghi chú", None),
        "encounter_id": ("Mã đợt điều trị", None),
        "author_user_id": ("Mã người viết", None),
        "note_type": ("Loại ghi chú", "'PROGRESS' (diễn biến), 'OBSERVATION' (theo dõi), 'TREATMENT' (điều trị)."),
        "severity_level": ("Mức độ", "'NORMAL', 'ATTENTION', 'URGENT' hoặc NULL."),
        "created_at": ("Thời điểm tạo", None),
        "patient_id": ("Mã bệnh nhân", None),
        "ward": ("Khoa", "Khoa của giường hiện tại."),
        "bed_code": ("Mã giường", None),
        "doctor": ("Bác sĩ phụ trách", None),
    }
    NOTE_METRICS = [
        ("so_ghi_chu", "Số ghi chú", "COUNT(*)", "Số ghi chú lâm sàng (dùng để đếm theo loại, mức độ, khoa...)."),
        ("so_ghi_chu_khan", "Số ghi chú khẩn (URGENT)", "SUM(CASE WHEN severity_level = 'URGENT' THEN 1 ELSE 0 END)", "Chỉ đếm ghi chú mức URGENT; muốn đếm theo từng mức độ thì dùng so_ghi_chu."),
        ("so_dot_dieu_tri_co_ghi_chu", "Số đợt điều trị có ghi chú", "COUNT(DISTINCT encounter_id)", None),
    ]
    apply(note, NOTE_METRICS, NOTE_COLUMNS, dttm=["created_at"])
    db.session.commit()
    print("BED", bed.id, [m.metric_name for m in bed.metrics])
    print("NOTE", note.id, [c.column_name for c in note.columns], [m.metric_name for m in note.metrics])
