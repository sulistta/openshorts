"""Launch durable local workers without polluting MCP stdio."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from .store import Store


class JobManager:
    """Submit jobs quickly; workers persist their own state on disk."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._tasks: set[asyncio.Task[None]] = set()
        self._workdir = Path(__file__).resolve().parents[1]

    async def submit(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = self.store.create_job(job_type, payload)
        task = asyncio.create_task(self._start_worker(str(job["job_id"])))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.store.public_job(job)

    async def _forward_output(
        self,
        stream: asyncio.StreamReader | None,
        job_id: str,
        prefix: str,
    ) -> None:
        if stream is None:
            return
        while line := await stream.readline():
            message = line.decode(errors="replace").rstrip()
            if message:
                print(f"[openshorts worker {job_id}] {prefix}{message}", file=sys.stderr, flush=True)

    async def _start_worker(self, job_id: str) -> None:
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "openshorts_mcp.worker",
                "--root",
                str(self.store.root),
                "--job",
                job_id,
                cwd=str(self._workdir),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            self.store.update_job(
                job_id,
                status="failed",
                error=f"Could not start local worker: {type(exc).__name__}: {exc}",
            )
            return

        stdout_task = asyncio.create_task(self._forward_output(process.stdout, job_id, ""))
        stderr_task = asyncio.create_task(self._forward_output(process.stderr, job_id, "stderr: "))
        return_code = await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        try:
            job = self.store.get_job(job_id)
            if job.get("status") in {"queued", "running"}:
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=f"Local worker stopped unexpectedly (exit code {return_code}).",
                )
        except Exception:
            # The worker may have been interrupted after an explicit project
            # deletion. There is no useful protocol action left to take.
            pass
