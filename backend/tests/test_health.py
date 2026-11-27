from tokenpilot.config import Settings
from tokenpilot.web.app import create_app


def test_health_reports_ok_when_database_is_reachable(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": True}


def test_health_reports_degraded_when_database_is_down():
    # Nothing listens on port 1, so the connection fails immediately.
    app = create_app(Settings(database_url="postgresql+psycopg2://x:x@localhost:1/none"))

    response = app.test_client().get("/health")

    assert response.status_code == 503
    assert response.get_json() == {"status": "degraded", "database": False}
