from fastapi.testclient import TestClient

from web.api import app


def test_app_route_serves_the_workbench_html():
    client = TestClient(app)
    response = client.get("/app")

    assert response.status_code == 200
    assert "学习与训练工作台" in response.text
    assert response.headers["content-type"].startswith("text/html")
    css = client.get("/app/app.css")
    script = client.get("/app/app.js")
    assert css.status_code == 200
    assert script.status_code == 200
    assert "metric-grid" in css.text
    assert "refreshWorkspace" in script.text
    assert "/plans" in script.text


def test_run_trace_is_not_rendered_in_frontend():
    client = TestClient(app)
    page = client.get("/app").text
    script = client.get("/app/app.js").text

    assert "RUN TRACE" not in page
    assert "trace-panel" not in page
    assert "trace-output" not in script
    assert "run-summary" not in script


def test_frontend_static_assets_are_served_with_no_cache():
    client = TestClient(app)
    page = client.get("/app")
    script = client.get("/app/app.js")

    assert page.headers.get("cache-control") == "no-cache"
    assert script.headers.get("cache-control") == "no-cache"


def test_document_upload_form_allows_multiple_files_and_immediate_return():
    client = TestClient(app)
    page = client.get("/app").text

    # 按钮就叫「上传」，且一次可以选择多个文件
    assert "上传并建立索引" not in page
    assert (
        'id="document-file" type="file" '
        'accept=".md,.txt,.pdf,text/markdown,text/plain,application/pdf" multiple required'
    ) in page
    assert ">上传</button>" in page


def test_document_list_shows_localized_state_and_actions():
    client = TestClient(app)
    script = client.get("/app/app.js").text

    # 状态徽标中文化
    assert 'pending: "索引中"' in script
    assert 'ready: "已完成"' in script
    assert 'failed: "失败"' in script
    # 失败可重试、可删除
    assert "data-retry-document" in script
    assert "retryDocumentById" in script
    assert "deleteDocumentById" in script
    # 隐藏已删除的资料行
    assert 'state !== "deleted"' in script
    # 有文档在索引中时轮询刷新；上传逐文件进行并给出进度
    assert "setInterval" in script
    assert "上传中" in script


def test_fitness_view_uses_workout_table_and_drops_single_form_and_csv_paste():
    client = TestClient(app)
    page = client.get("/app").text
    script = client.get("/app/app.js").text

    # 旧的单次训练表单与 CSV 粘贴入口彻底移除
    assert "记录训练" not in page
    assert "workout-form" not in page
    assert '$("workout-form")' not in script
    assert "fitness-csv" not in page
    assert "fitness-csv" not in script
    assert "BULK IMPORT" not in page
    # 训练表格 + 六列表头
    assert "训练表格" in page
    assert 'id="workout-rows"' in page
    for header in ("日期", "部位", "动作", "重量 kg", "次数", "组数"):
        assert header in page
    # 体重小表单保留
    assert 'id="weight-form"' in page


def test_workout_table_offers_presets_and_row_controls():
    client = TestClient(app)
    page = client.get("/app").text
    script = client.get("/app/app.js").text

    assert 'id="body-part-options"' in page
    assert 'id="exercise-options"' in page
    for body_part in ("胸", "背", "腿", "肩", "手臂", "核心", "有氧"):
        assert f'<option value="{body_part}"></option>' in page
    for exercise in ("卧推", "引体向上", "深蹲", "硬拉", "跑步"):
        assert f'<option value="{exercise}"></option>' in page
    assert 'id="add-workout-row"' in page
    assert "addWorkoutRow" in script
    assert "data-remove-workout-row" in script


def test_workout_table_submits_generated_csv_to_import_commit():
    client = TestClient(app)
    script = client.get("/app/app.js").text

    # 前端把表格拼成 CSV 后走幂等导入接口；组数展开为多行
    assert "workout_date,exercise_name,weight_kg,reps,body_part,recorded_on" in script
    assert '"/fitness/import/commit"' in script
    assert "collectWorkoutRows" in script
    assert "workoutCsvText" in script
    assert "set < Number(row.sets)" in script
    # 行校验与空行跳过
    assert "请补全第" in script
    assert "请先填写训练记录" in script


def test_exercise_chart_supports_three_view_switch():
    client = TestClient(app)
    page = client.get("/app").text
    script = client.get("/app/app.js").text

    assert 'id="exercise-chart-view"' in page
    for option in ('value="total"', 'value="daily"', 'value="per-exercise"'):
        assert option in page
    for label in ("各动作总量", "各日期总训练量", "每动作训练量变化"):
        assert label in page
    assert "drawMultiLine" in script
    assert 'view === "daily"' in script
    assert 'view === "per-exercise"' in script


def test_long_panels_scroll_internally():
    client = TestClient(app)
    css = client.get("/app/app.css").text

    assert ".list-stack { display: grid; max-height: 360px; overflow-y: auto; }" in css
    assert ".data-list { max-height: 220px; overflow-y: auto; margin-top: 15px; }" in css
    assert ".workout-table-scroll { max-height: 320px; overflow-y: auto; }" in css
