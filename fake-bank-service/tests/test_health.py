from flask import Flask

from routes.health import health_bp


def test_health_returns_ok_json():
    app = Flask(__name__)
    app.register_blueprint(health_bp)

    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.is_json
    assert response.content_type.startswith("application/json")
    assert response.get_json() == {"status": "ok"}
