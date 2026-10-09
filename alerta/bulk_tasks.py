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


def _snapshot(alert: Alert) -> Dict[str, Any]:
    return {
        "id": alert.id,
        "resource": alert.resource,
        "event": alert.event,
        "severity": alert.severity,
        "environment": alert.environment or "",
        "text": alert.text or "",
        "status": alert.status or "open",
        "value": str(alert.value or ""),
        "group": alert.group or "",
        "duplicate_count": int(alert.duplicate_count or 0),
        "tags": list(alert.tags or []),
        "service": list(alert.service or []),
        "attributes": dict(alert.attributes or {}),
        "create_time": alert.create_time.isoformat() if alert.create_time else None,
        "receive_time": alert.receive_time.isoformat() if alert.receive_time else None,
    }


def _handle_bulk_action(
    alert_ids: List[str], action: str, text: str, login: str
) -> Dict[str, Any]:
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
        _snapshot(a) for a in alerts
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