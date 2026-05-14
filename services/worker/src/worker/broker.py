"""Dramatiq Redis broker for the worker service.

Binds the worker process to the same Redis queue the API publishes to.
"""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from worker.config import settings

redis_broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(redis_broker)
