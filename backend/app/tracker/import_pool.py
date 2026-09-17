"""Limit document parsing to one killable process alongside regular refreshes."""

import asyncio
import json
import os
import sys
from pathlib import Path

from fastapi import HTTPException

from .limits import MAX_CSV_BYTES, MAX_PREVIEW_BYTES
from .urls import DiscoveryError
from .workers import await_worker


class DocumentImporter:
    def __init__(self, timeout=20):
        self.busy = False
        self.timeout = timeout

    async def parse(self, data, filename, **options):
        if not data or len(data) > MAX_CSV_BYTES:
            raise DiscoveryError("Choose a non-empty file up to 4 MB.")
        if self.busy:
            raise HTTPException(
                429,
                "Another document is being processed. Try again shortly.",
                headers={"Retry-After": "5"},
            )
        self.busy = True
        process = None
        try:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}
            }
            environment.update(
                PYTHONDONTWRITEBYTECODE="1",
                OPENBLAS_NUM_THREADS="1",
                OMP_NUM_THREADS="1",
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.tracker.import_worker",
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
            )
            message = (
                json.dumps({"filename": filename, **options}).encode() + b"\n" + data
            )
            async with asyncio.timeout(self.timeout):
                output, _ = await process.communicate(message)
            if process.returncode or len(output) > MAX_PREVIEW_BYTES:
                raise DiscoveryError(
                    "This document exceeded the processing limit or could not be read. Split it into smaller files."
                )
            result = json.loads(output)
            if result.get("error"):
                raise DiscoveryError(result["error"])
            return result["result"]
        except TimeoutError as exc:
            raise DiscoveryError(
                "Document processing took too long. Split the file or paste its text."
            ) from exc
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DiscoveryError(
                "The document could not be processed. Try again or paste its text."
            ) from exc
        finally:
            try:
                if process and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    # Request disconnects use level cancellation. Retain admission
                    # until the child is reaped even if cancellation repeats here.
                    await await_worker(asyncio.create_task(process.wait()))
            finally:
                self.busy = False
