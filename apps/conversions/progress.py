# "Job progress cache and DB heartbeat helpers."
from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

PROGRESS_TTL_SECONDS = 60 * 60


def progress_key(job_public_id) -> str:
    return f"job-progress:{job_public_id}"


class ProgressReporter:
    def __init__(
        self,
        job_public_id,
        *,
        job_pk: int | None = None,
        claim_generation: int | None = None,
        min_interval: float = 1.0,
        min_percent_delta: int = 1,
    ) -> None:
        self.job_public_id = job_public_id
        self.job_pk = job_pk
        self.claim_generation = claim_generation
        self.min_interval = min_interval
        self.min_percent_delta = min_percent_delta
        self.last_write = 0.0
        self.last_percent = -1

    def __call__(self, percent: int, message: str = "") -> None:
        now = time.monotonic()
        percent = max(0, min(int(percent), 100))
        # Always emit the first update (last_percent starts at -1). On some hosts
        # time.monotonic() begins below min_interval, which would otherwise drop it.
        if (
            self.last_percent >= 0
            and now - self.last_write < self.min_interval
            and abs(percent - self.last_percent) < self.min_percent_delta
            and percent < 100
        ):
            return
        cache.set(
            progress_key(self.job_public_id),
            {"percent": percent, "message": message, "updated_at": time.time()},
            PROGRESS_TTL_SECONDS,
        )
        # Also persist to the DB (throttled by the guard above). The cache is per-process
        # by default (LocMemCache), so the web tier cannot read a worker's cache; the DB is
        # the only channel that reliably surfaces fine-grained progress across processes.
        # Crucially this also refreshes heartbeat_at so a long-running conversion is not
        # mistaken for a dead worker and reaped mid-flight.
        self._persist_to_db(percent, message)
        self.last_write = now
        self.last_percent = percent

    def _persist_to_db(self, percent: int, message: str) -> None:
        if self.job_pk is None or self.claim_generation is None:
            return
        from .models import ConversionJob

        try:
            # Fenced by claim_generation + PROCESSING: a superseded/stale worker must not
            # revive a claim it no longer owns nor mutate a job that has moved on.
            ConversionJob.objects.filter(
                pk=self.job_pk,
                claim_generation=self.claim_generation,
                status=ConversionJob.Status.PROCESSING,
            ).update(
                progress_percent=percent,
                progress_message=message[:160],
                heartbeat_at=timezone.now(),
            )
        except Exception:  # progress is best-effort; never fail the conversion over it
            logger.exception("Failed to persist progress for job %s", self.job_public_id)


def get_cached_progress(job) -> dict:
    cached = cache.get(progress_key(job.public_id))
    if cached:
        return cached
    return {"percent": job.progress_percent, "message": job.progress_message, "updated_at": None}
