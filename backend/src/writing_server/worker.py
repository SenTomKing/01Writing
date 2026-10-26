"""Linux Celery worker 与轻量投递循环；本机另提供显式开发运行器。"""

import time

from celery import Celery
from sqlalchemy import select

from .database import Database
from .jobs import dispatch, process_task, recover
from .settings import Settings
from .tables import Task, now

settings = Settings.load()
celery = Celery("writing", broker=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={
        "visibility_timeout": settings.lease_seconds + 60,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    },
    task_soft_time_limit=settings.lease_seconds - 10,
    task_time_limit=settings.lease_seconds,
)


@celery.task(name="writing.generate")
def generate_task(task_id):
    db = Database(settings)
    try:
        return process_task(db, settings, task_id)
    finally:
        db.close()


def dispatch_loop(*, local=False, once=False):
    db = Database(settings)
    try:
        while True:
            recover(db, settings)
            if local:
                with db.sessions() as session:
                    ids = list(
                        session.scalars(
                            select(Task.id).where(Task.status == "queued", Task.dispatch_after <= now()).limit(10)
                        )
                    )
                for task_id in ids:
                    process_task(db, settings, task_id)
            else:
                dispatch(db, lambda task_id: generate_task.apply_async(args=[task_id], retry=False))
            if once:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
