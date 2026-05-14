"""Dramatiq Redis broker for the API service.

The API enqueues jobs (PDF gen, Discord notify, knowledge ingest) here.
The worker process pulls and executes them.

Must be imported BEFORE any module that defines an actor — see main.py.
"""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from api.config import settings

redis_broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(redis_broker)
