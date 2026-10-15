"""List main quantity analyses that a server restart would interrupt.

Run before restarting the web server during a deploy. Exit status 1 means at least one
analysis is running in a live worker; restarting then interrupts it (its owner can resume it
from the kept uploads, but the AI step in progress is paid again).
"""
import time

from django.core.management.base import BaseCommand

from core import quantity_views
from core.quantity_state import worker_alive


class Command(BaseCommand):
    help = "List running quantity analyses before a server restart (exit 1 when any is running)."

    def handle(self, *args, **options):
        state = quantity_views._quantity_state()
        with state.connection() as connection:
            rows = connection.execute("SELECT key FROM quantity_state WHERE namespace='job' AND expires>?",
                                      (time.time(),)).fetchall()
        running = []
        for (job_id,) in rows:
            job = state.get("job", job_id) or {}
            if job.get("operation") != "api_run_quantity" or state.get("result", job_id) is not None:
                continue
            if worker_alive(job.get("pid")):
                progress = state.get("progress", job_id) or {}
                running.append((job.get("created_at") or 0, job_id, progress.get("label") or "-"))
        for created_at, job_id, label in sorted(running):
            minutes = (time.time() - created_at) / 60
            self.stdout.write(f"running {job_id[:24]} started {minutes:.0f} min ago: {label}")
        self.stdout.write(f"{len(running)} running quantity analysis(es)")
        if running:
            raise SystemExit(1)
