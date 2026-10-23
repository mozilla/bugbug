from hackbot_runtime import broker
from hackbot_runtime.broker import run_broker
from pydantic_settings import BaseSettings


class _Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8765


def test_run_broker_serves_app_built_from_settings(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    app = object()
    served = {}

    def fake_uvicorn_run(app, host, port, log_config):
        served.update(app=app, host=host, port=port)

    monkeypatch.setattr(broker.uvicorn, "run", fake_uvicorn_run)

    run_broker(lambda settings: app, _Settings)

    assert served == {"app": app, "host": "0.0.0.0", "port": 9999}
