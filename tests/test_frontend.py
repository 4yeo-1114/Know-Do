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
