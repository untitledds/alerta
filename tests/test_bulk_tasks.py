from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture()
def app_context(monkeypatch):
    flask_app = Flask(__name__)
    flask_app.config.update(
        {
            "CELERY_BROKER_URL": "memory://",
            "CELERY_RESULT_BACKEND": "cache+memory://",
            "CELERY_TASK_ALWAYS_EAGER": True,
            "CELERY_TASK_EAGER_PROPAGATES": True,
        }
    )

    import alerta.plugins as alerta_plugins

    monkeypatch.setattr(alerta_plugins, "app", flask_app)

    with flask_app.app_context():
        yield flask_app


@pytest.fixture()
def fake_plugin():
    calls = []

    class FakePlugin:
        name = "fake"

        def take_action(self, alert, action, text, **kwargs):
            calls.append(
                {
                    "alert_id": alert.id,
                    "action": action,
                    "text": text,
                    "bulk_alert_ids": list(alert.attributes.get("bulk_alert_ids", [])),
                }
            )
            return alert, action, text

    plugin = FakePlugin()
    plugin.calls = calls
    return plugin


@pytest.fixture()
def fake_alerts():
    from alerta.models.alert import Alert

    def make(alert_id: str, resource: str) -> Alert:
        a = Alert(
            resource=resource,
            event="test",
            severity="warning",
            attributes={},
            tags=[],
        )
        a.id = alert_id
        return a

    return {
        "a1": make("id-1", "host-1"),
        "a2": make("id-2", "host-2"),
        "a3": make("id-3", "host-3"),
    }


def test_bulk_action_calls_take_action_once(
    app_context, fake_plugin, fake_alerts, monkeypatch
):
    from alerta import bulk_tasks

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: fake_alerts.get(alert_id),
    )

    with patch("alerta.app.plugins") as mock_plugins:
        mock_plugins.routing.return_value = ([fake_plugin], {})

        result = bulk_tasks.bulk_action_alerts(
            alerts=["id-1", "id-2", "id-3"],
            action="bulk_test",
            text="test-payload",
            timeout=None,
            login="ivanov",
        )

    assert len(fake_plugin.calls) == 1, (
        f"take_action должен быть вызван 1 раз, но вызван {len(fake_plugin.calls)} раз"
    )

    call = fake_plugin.calls[0]
    assert call["alert_id"] == "id-1"
    assert call["action"] == "bulk_test"
    assert call["text"] == "test-payload"
    assert call["bulk_alert_ids"] == ["id-1", "id-2", "id-3"]


def test_bulk_action_returns_summary(
    app_context, fake_plugin, fake_alerts, monkeypatch
):
    from alerta import bulk_tasks

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: fake_alerts.get(alert_id),
    )

    with patch("alerta.app.plugins") as mock_plugins:
        mock_plugins.routing.return_value = ([fake_plugin], {})

        result = bulk_tasks.bulk_action_alerts(
            alerts=["id-1", "id-2"],
            action="bulk_test",
            text="",
            timeout=None,
            login="ivanov",
        )

    assert result["status"] == "ok"
    assert sorted(result["updated"]) == ["id-1", "id-2"]
    assert result["errors"] == []


def test_bulk_action_handles_missing_alerts(
    app_context, fake_plugin, fake_alerts, monkeypatch
):

    from alerta import bulk_tasks

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: fake_alerts.get(alert_id),  # id-999 → None
    )

    with patch("alerta.app.plugins") as mock_plugins:
        mock_plugins.routing.return_value = ([fake_plugin], {})

        result = bulk_tasks.bulk_action_alerts(
            alerts=["id-1", "id-999", "id-3"],
            action="bulk_test",
            text="",
            timeout=None,
            login="ivanov",
        )

    assert result["status"] == "partial"
    assert sorted(result["updated"]) == ["id-1", "id-3"]
    assert len(result["errors"]) == 1
    assert "id-999" in result["errors"][0]


def test_bulk_action_handles_plugin_exception(app_context, fake_alerts, monkeypatch):
    from alerta import bulk_tasks

    class BrokenPlugin:
        name = "broken"

        def take_action(self, alert, action, text, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: fake_alerts.get(alert_id),
    )

    with patch("alerta.app.plugins") as mock_plugins:
        mock_plugins.routing.return_value = ([BrokenPlugin()], {})

        result = bulk_tasks.bulk_action_alerts(
            alerts=["id-1"],
            action="bulk_test",
            text="",
            timeout=None,
            login="ivanov",
        )

    assert result["status"] == "partial"
    assert len(result["errors"]) == 1
    assert "broken: boom" in result["errors"][0]


def test_bulk_action_handles_not_implemented(app_context, fake_alerts, monkeypatch):
    from alerta import bulk_tasks

    class NoopPlugin:
        name = "noop"

        def take_action(self, alert, action, text, **kwargs):
            raise NotImplementedError

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: fake_alerts.get(alert_id),
    )

    with patch("alerta.app.plugins") as mock_plugins:
        mock_plugins.routing.return_value = ([NoopPlugin()], {})

        result = bulk_tasks.bulk_action_alerts(
            alerts=["id-1"],
            action="bulk_test",
            text="",
            timeout=None,
            login="ivanov",
        )

    assert result["status"] == "ok"
    assert result["errors"] == []


def test_bulk_action_no_alerts(app_context, fake_alerts, monkeypatch):
    from alerta import bulk_tasks

    monkeypatch.setattr(
        bulk_tasks.Alert,
        "find_by_id",
        lambda alert_id: None,
    )

    result = bulk_tasks.bulk_action_alerts(
        alerts=["id-1", "id-2"],
        action="bulk_test",
        text="",
        timeout=None,
        login="ivanov",
    )

    assert result["status"] == "error"
    assert len(result["errors"]) == 2
    assert result["updated"] == []
