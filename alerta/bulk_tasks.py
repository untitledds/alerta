"""Bulk-action Celery tasks.

Handle actions with the `bulk_` prefix: call plugin.take_action
ONCE for the whole batch of alerts.

"""
import logging
from typing import Any, Dict, List, Optional

from flask import g

from alerta.app import create_celery_app
from alerta.models.alert import Alert

logger = logging.getLogger(__name__)

celery = create_celery_app()


def _handle_bulk_action(
    alert_ids: List[str], action: str, text: str, login: str
) -> Dict[str, Any]:
    """Вызывает plugin.take_action(head_alert, action, text) один раз."""
    from alerta.app import plugins

    g.login = login

    alerts: List[Alert] = []
    errors: List[str] = []
    for alert_id in alert_ids:
        alert = Alert.find_by_id(alert_id)
        if not alert:
            errors.append(f"Alert {alert_id} not found")
            continue
        alerts.append(alert)

    if not alerts:
        return {"status": "error", "errors": errors, "created": None}

    head_alert = alerts[0]
    head_alert.attributes["bulk_alert_ids"] = [a.id for a in alerts]
    head_alert.attributes["bulk_alerts"] = [
        {
            "id": a.id,
            "resource": a.resource,
            "event": a.event,
            "severity": a.severity,
            "text": a.text,
            "attributes": a.attributes,
        }
        for a in alerts
    ]

    wanted_plugins, wanted_config = plugins.routing(head_alert)
    result_alert = head_alert

    for plugin in wanted_plugins:
        try:
            updated = plugin.take_action(
                head_alert, action, text, config=wanted_config
            )
        except NotImplementedError:
            continue
        except Exception as e:
            logger.exception("Plugin %s failed on %s", plugin.name, action)
            errors.append(f"{plugin.name}: {e}")
            continue
        if isinstance(updated, tuple) and len(updated) >= 3:
            result_alert = updated[0]

    return {
        "status": "ok" if not errors else "partial",
        "updated": [a.id for a in alerts],
        "errors": errors,
        "created": getattr(result_alert, "id", None),
    }


@celery.task
def bulk_action_alerts(
    alerts: List[str],
    action: str,
    text: str,
    timeout: Optional[int],
    login: str,
) -> Dict[str, Any]:
    logger.info(
        "BULK_ACTION: action=%r, alerts_count=%d, login=%r",
        action, len(alerts), login,
    )
    return _handle_bulk_action(alerts, action, text, login)