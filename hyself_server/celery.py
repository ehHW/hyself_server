import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hyself_server.settings")

app = Celery("hyself_server")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

if os.name == "nt":
	app.conf.worker_pool = "solo"
	app.conf.worker_concurrency = 1