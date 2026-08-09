"""RQ worker entrypoint.

The worker shares the backend image and runs inside Docker (Linux). It is not
meant to be run directly on the Windows host. Loading ``backend.app.main``
ensures the FastAPI app (and therefore all SQLAlchemy models) is imported so
the worker sees the same configuration and models as the web process.
"""

import os

from redis import Redis
from rq import Worker


def run() -> None:
    redis = Redis.from_url(os.environ["REDIS_URL"])
    queues = [
        queue.strip()
        for queue in os.getenv("RQ_QUEUES", "default").split(",")
        if queue.strip()
    ] or ["default"]
    Worker(queues, connection=redis).work()


if __name__ == "__main__":
    run()
