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
