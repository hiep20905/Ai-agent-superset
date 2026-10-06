"""Regression test of the assistant against real Superset data and the
configured model: each case asks a question (optionally after earlier turns)
and checks which tools ran, with which effective arguments, and the answer.

Run inside the Superset container (the extension must be installed):
    docker cp eval <container>:/tmp/eval
    docker exec <container> python /tmp/eval/run_eval.py /tmp/eval/cases.hospital.json \
        [--dashboard 13] [--only id1,id2] [--category "Thời gian"] [--user admin] \
        [--report /tmp/eval/report.json]

Case format (JSON list), every "expect" key optional:
    {"id": "...", "category": "...", "question": "..." | "turns": ["q1", "q2"],
     "dashboard": 13,
     "history": [{"role": "user", "text": "..."}, {"role": "model", "text": "..."}],
     "expect": {
        "tools": ["search_datasets"],         # each must have been called
        "no_tools": true,                     # no tool at all (knowledge/refusal)
        "no_query": true,                     # no query_dataset / draw_chart
        "query": {                            # one successful query (query_dataset,
            "dataset_id": 24,                 #   draw_chart or query_chart) must match all:
            "chart_id": 122,
            "metrics": ["so_benh_nhan", "tong_x|x_col:sum"],   # "a|b": either
            "group_by": ["ward"],             # in group_by or columns
            "filters": {"ward": ["Khoa Tim mạch"]},   # column -> values (accent-insensitive)
            "time_range": null | "set" | "{this_year}" | "2026-07-01 : 2026-08-01",
            "time_grain": "month",
            "min_rows": 1},
        "answer_contains": ["28"],            # all, accent/case-insensitive
        "answer_any": ["không", "từ chối"],   # at least one
        "answer_not_contains": ["2024"]}}
"{key}" in time_range is one of timerange.periods() keys (today, last_week,
this_year...), so cases do not depend on the day they run.
"""

import argparse
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf8")

QUERY_TOOLS = ("query_dataset", "draw_chart", "query_chart")


def run_case(case: dict, default_dashboard, modules) -> dict:
    catalog, prompt, providers, tools, history_turns = modules
    turns = case.get("turns") or [case["question"]]
    dashboard = case.get("dashboard", default_dashboard)
    # Fixed earlier turns (e.g. bad answers the model might copy), then live turns.
    history: list[dict] = list(case.get("history", []))
    seeded = next((t["text"] for t in reversed(history) if t["role"] == "user"), "")
    answer, state, started = "", None, time.time()
    for i, question in enumerate(turns):
        ctx = catalog.load_context(dashboard)
        previous = turns[i - 1] if i else seeded
        state = tools.RequestState(question=question, context=ctx, recent=previous)
        answer = ""
        for ev in providers.stream(prompt.build(ctx, catalog.can_search(ctx)), question,
                                   history[-2 * history_turns:], state):
            if ev["type"] == "token":
                answer += ev["text"]
            elif ev["type"] == "discard":
                answer = ""
        history += [{"role": "user", "text": question}, {"role": "model", "text": answer}]
    return {"answer": answer.strip(), "calls": state.calls, "seconds": time.time() - started}


def _metric_key(m) -> str:
    if isinstance(m, dict):
        name = str(m.get("name") or m.get("metric") or m.get("column") or "")
        agg = str(m.get("aggregation") or "").lower()
        return f"{name}:{agg}" if agg else name
    return str(m)


def _query_problems(args: dict, call: dict, want: dict, fold, ranges) -> list[str]:
    problems = []
    dataset_id = args.get("dataset_id") or call.get("dataset_id")
    if "dataset_id" in want and str(dataset_id) != str(want["dataset_id"]):
        problems.append(f"dataset_id={dataset_id}")
    if "chart_id" in want and str(args.get("chart_id")) != str(want["chart_id"]):
        problems.append(f"chart_id={args.get('chart_id')} (cần {want['chart_id']})")
    have_metrics = {_metric_key(m) for m in args.get("metrics") or []}
    for m in want.get("metrics", []):
        # "a|b": either form is right (a saved metric and its column aggregation).
        if not any(alt in have_metrics for alt in m.split("|")):
            problems.append(f"thiếu metric {m}")
    cols = {str(c) for c in (args.get("group_by") or []) + (args.get("columns") or [])}
    for c in want.get("group_by", []):
        if c not in cols:
            problems.append(f"thiếu group_by {c}")
    for col, values in want.get("filters", {}).items():
        got = []
        for f in args.get("filters") or []:
            if str(f.get("col")) == col:
                v = f.get("vals") if f.get("vals") is not None else f.get("val")
                got += v if isinstance(v, list) else [v]
        missing = [v for v in values if fold(v) not in {fold(g) for g in got}]
        if missing:
            problems.append(f"lọc {col} thiếu {missing} (có {got})")
    if "time_range" in want:
        tr = str(args.get("time_range") or "")
        effective = "" if tr.lower() in ("", "no filter") else tr
        expected = want["time_range"]
        if expected is None and effective:
            problems.append(f"không được lọc thời gian (có '{effective}')")
        elif expected == "set" and not effective:
            problems.append("thiếu time_range")
        elif expected not in (None, "set"):
            expected = expected.format(**ranges)
            if effective.replace(" ", "") != expected.replace(" ", ""):
                problems.append(f"time_range='{effective}', cần '{expected}'")
    if "time_grain" in want and str(args.get("time_grain") or "") != want["time_grain"]:
        problems.append(f"time_grain={args.get('time_grain')}")
    if call.get("row_count", 0) < want.get("min_rows", 0):
        problems.append(f"chỉ {call.get('row_count', 0)} dòng")
    return problems


def check(case: dict, result: dict, fold, ranges) -> list[str]:
    want = case.get("expect", {})
    calls, answer = result["calls"], result["answer"]
    names = [c["tool"] for c in calls]
    problems = []
    for tool in want.get("tools", []):
        if tool not in names:
            problems.append(f"không gọi {tool}")
    if want.get("no_tools") and calls:
        problems.append(f"không cần tool nhưng đã gọi {names}")
    if want.get("no_query") and any(n in QUERY_TOOLS for n in names):
        problems.append("không cần query nhưng đã query")
    if "query" in want:
        queries = [c for c in calls if c["tool"] in QUERY_TOOLS and c["ok"]]
        if not queries:
            problems.append("không có truy vấn thành công")
        else:
            attempts = [_query_problems(c["args"], c, want["query"], fold, ranges) for c in queries]
            best = min(attempts, key=len)
            if best:
                problems.append("truy vấn sai: " + "; ".join(best))
    folded = fold(answer)
    for text in want.get("answer_contains", []):
        if fold(text) not in folded:
            problems.append(f"câu trả lời thiếu '{text}'")
    if want.get("answer_any") and not any(fold(t) in folded for t in want["answer_any"]):
        problems.append(f"câu trả lời không có cụm nào trong {want['answer_any']}")
    for text in want.get("answer_not_contains", []):
        if fold(text) in folded:
            problems.append(f"câu trả lời không được có '{text}'")
    if not answer:
        problems.append("không có câu trả lời")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases")
    parser.add_argument("--dashboard", type=int, default=None)
    parser.add_argument("--only", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--report", default="")
    opts = parser.parse_args()

    with open(opts.cases, encoding="utf8") as f:
        cases = json.load(f)
    if opts.only:
        wanted = set(opts.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if opts.category:
        cases = [c for c in cases if c.get("category") == opts.category]

    from superset.app import create_app

    app = create_app()
    with app.test_request_context():
        from flask import g
        from superset import security_manager

        g.user = security_manager.find_user(opts.user)
        from demo.hospital_chat import catalog, config, prompt, providers, timerange, tools
        from demo.hospital_chat.text import fold

        ranges = {k: r for k, _, r in timerange.periods()}
        modules = (catalog, prompt, providers, tools, config.history_turns())
        model = config.ollama_model() if config.provider() == "ollama" else config.gemini_models()[0]
        print(f"Model: {config.provider()} / {model} - {len(cases)} câu\n")

        results = []
        for case in cases:
            try:
                result = run_case(case, opts.dashboard, modules)
                problems = check(case, result, fold, ranges)
            except Exception as ex:  # pylint: disable=broad-except
                result, problems = {"answer": "", "calls": [], "seconds": 0}, [f"lỗi: {ex!r}"]
            guard = sum(1 for c in result["calls"] if c.get("dropped_time_range"))
            status = "PASS" if not problems else "FAIL"
            print(f"[{status}] {case['id']} ({result['seconds']:.0f}s)"
                  + (f" - chặn time_range {guard} lần" if guard else ""))
            for p in problems:
                print(f"       - {p}")
            if problems:
                for c in result["calls"]:
                    print(f"       · {c['tool']} {json.dumps(c['args'], ensure_ascii=False)[:300]}"
                          f" -> {c['summary']}")
                print(f"       · Trả lời: {result['answer'][:300]!r}")
            results.append({**case, **result, "problems": problems, "guard_fired": guard})

        print("\n== Tổng kết")
        categories = sorted({r.get("category", "") for r in results})
        for cat in categories:
            rows = [r for r in results if r.get("category", "") == cat]
            ok = sum(1 for r in rows if not r["problems"])
            print(f"  {cat or '(không nhóm)'}: {ok}/{len(rows)}")
        ok = sum(1 for r in results if not r["problems"])
        guards = sum(r["guard_fired"] for r in results)
        print(f"  TỔNG: {ok}/{len(results)} đạt; chốt chặn time_range kích hoạt {guards} lần")
        if opts.report:
            with open(opts.report, "w", encoding="utf8") as f:
                json.dump({"model": model, "results": results}, f, ensure_ascii=False,
                          indent=2, default=str)
            print(f"  Báo cáo: {opts.report}")


if __name__ == "__main__":
    main()
