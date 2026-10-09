"""Bulk-action Celery tasks.

Handle actions with the `bulk_` prefix: call plugin.take_action
ONCE for the whole batch of alerts.

The plugin receives a `BulkAlertEnvelope` instance — a virtual, non-persisted
container that duck-types the ``Alert`` interface used by plugins and routing,
but carries the full list of real ``Alert`` objects in ``alerts``. This keeps
the API backwards compatible with plugins that only read ``alert.attributes``
while giving bulk-aware plugins direct access to real DB-backed alerts.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from flask import g

from alerta.app import create_celery_app
from alerta.models.alert import Alert

logger = logging.getLogger(__name__)

celery = create_celery_app()


def _snapshot(alert: Alert) -> dict[str, Any]:
    """Serialize an Alert into a plain dict for cross-process transport."""
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


def _resolve_bulk_id(alerts: list[Alert]) -> str:
    """Derive a stable bulk identifier from the sorted set of alert ids."""
    ids = sorted(a.id for a in alerts if a.id)
    if not ids:
        return "general"
    digest = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()
    return f"bulk-{digest[:12]}"


@dataclass
class BulkAlertEnvelope:
    """Virtual, non-persisted container replacing the head alert for bulk ops.

    Duck-types the subset of the ``Alert`` interface used by plugins and
    ``plugins.routing`` (``id``, ``resource``, ``event``, ``severity``,
    ``environment``, ``tags``, ``service``, ``status``, ``attributes`` ...),
    while exposing the real DB-backed alerts via ``alerts``.

    Never persisted: ``save`` and ``update`` raise ``NotImplementedError``.
    The ``attributes`` dict carries the bulk payload:

    - ``bulk_id`` — stable identifier for the batch;
    - ``bulk_alert_ids`` — ids of ALL alerts in the batch;
    - ``bulk_alerts`` — snapshots of the batch EXCLUDING the head alert,
      matching the contract expected by ``EnrichedAlert`` consumers;
    - ``bulk_all_alerts`` — snapshots of ALL alerts, including the head.
    """

    id: str
    action: str
    text: str
    alerts: list[Alert]
    bulk_id: str
    resource: str = ""
    event: str = ""
    environment: str = ""
    severity: str = ""
    status: str = "open"
    value: str = ""
    group: str = ""
    tags: list[str] = field(default_factory=list)
    service: list[str] = field(default_factory=list)
    duplicate_count: int = 0
    create_time: datetime | None = None
    receive_time: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Inherit routing-relevant fields from the head alert."""
        if not self.alerts:
            raise ValueError("BulkAlertEnvelope requires at least one alert")
        head = self.alerts[0]
        self.resource = getattr(head, "resource", "") or ""
        self.event = getattr(head, "event", "") or ""
        self.environment = getattr(head, "environment", "") or ""
        self.severity = getattr(head, "severity", "") or ""
        self.status = getattr(head, "status", None) or "open"
        self.value = str(getattr(head, "value", "") or "")
        self.group = getattr(head, "group", "") or ""
        self.tags = list(getattr(head, "tags", []) or [])
        self.service = list(getattr(head, "service", []) or [])
        self.duplicate_count = int(getattr(head, "duplicate_count", 0) or 0)
        self.create_time = getattr(head, "create_time", None)
        self.receive_time = getattr(head, "receive_time", None)

        snapshots: list[dict[str, Any]] = []
        for alert in self.alerts:
            snap = _snapshot(alert)
            snap.setdefault("attributes", {})
            snap["attributes"].setdefault("bulk_id", self.bulk_id)
            snapshots.append(snap)

        inherited = dict(getattr(head, "attributes", {}) or {})
        inherited.update(
            {
                "bulk_id": self.bulk_id,
                "bulk_alert_ids": [a.id for a in self.alerts],
                "bulk_alerts": snapshots[1:],
                "bulk_all_alerts": snapshots,
            }
        )
        self.attributes = inherited

    @property
    def head_alert(self) -> Alert:
        """Return the real DB-backed head alert."""
        return self.alerts[0]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persisting a virtual envelope is forbidden."""
        raise NotImplementedError("BulkAlertEnvelope must not be saved")

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Persisting a virtual envelope is forbidden."""
        raise NotImplementedError("BulkAlertEnvelope must not be updated")

    def update_attributes(self, attributes: dict[str, Any]) -> None:
        """Propagate attribute updates to all real alerts in the batch."""
        for alert in self.alerts:
            alert.update_attributes(dict(attributes))


def _handle_bulk_action(
    alert_ids: list[str], action: str, text: str, login: str
) -> dict[str, Any]:
    """Resolve alerts, build the envelope and dispatch to plugins once."""
    from alerta.app import plugins

    g.login = login

    alerts: list[Alert] = []
    errors: list[str] = []
    for alert_id in alert_ids:
        alert = Alert.find_by_id(alert_id)
        if not alert:
            errors.append(f"Alert {alert_id} not found")
            continue
        alerts.append(alert)

    if not alerts:
        return {"status": "error", "errors": errors, "updated": [], "created": None, "bulk_id": None,}

    bulk_id = _resolve_bulk_id(alerts)

    envelope = BulkAlertEnvelope(
        id=f"bulk-{bulk_id}",
        action=action,
        text=text,
        alerts=alerts,
        bulk_id=bulk_id,
    )

    routable = cast(Alert, envelope)
    wanted_plugins, wanted_config = plugins.routing(routable)
    result_alert: Any = envelope

    for plugin in wanted_plugins:
        try:
            updated = plugin.take_action(routable, action, text, config=wanted_config)
        except NotImplementedError:
            continue
        except Exception as e:
            logger.exception("Plugin %s failed on %s", plugin.name, action)
            errors.append(f"{plugin.name}: {e}")
            continue
        if isinstance(updated, tuple) and len(updated) >= 3:
            result_alert = updated[0]

    created_id = getattr(result_alert, "id", None)
    if isinstance(result_alert, BulkAlertEnvelope):
        created_id = result_alert.head_alert.id

    return {
        "status": "ok" if not errors else "partial",
        "updated": [a.id for a in alerts],
        "errors": errors,
        "created": created_id,
        "bulk_id": bulk_id,
    }


@celery.task
def bulk_action_alerts(
    alerts: list[str],
    action: str,
    text: str,
    timeout: int | None,
    login: str,
) -> dict[str, Any]:
    """Celery entrypoint for bulk actions over a list of alert ids."""
    logger.info(
        "BULK_ACTION: action=%r, alerts_count=%d, login=%r",
        action,
        len(alerts),
        login,
    )
    return _handle_bulk_action(alerts, action, text, login)
