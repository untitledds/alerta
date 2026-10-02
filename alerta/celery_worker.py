"""Celery worker entrypoint.

Импортирует оба модуля задач, чтобы Celery их зарегистрировал.
"""
from alerta.app import create_app, create_celery_app

flask_app = create_app()
celery_app = create_celery_app(flask_app)

# Регистрация задач
import alerta.tasks       # noqa: F401  — upstream action_alerts
import alerta.bulk_tasks  # noqa: F401  — bulk_action_alerts