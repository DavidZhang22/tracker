"""Run bounded blocking scan work without blocking the HTTP event loop."""

import asyncio

from anyio import CancelScope


async def run_blocking(function, *args, **kwargs):
    # to_thread copies request context (including the scan budget). A cancelled
    # request must retain its admission/locks until its worker really stops;
    # otherwise repeated disconnects could create unlimited background work.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    return await await_worker(task)


async def await_worker(task):
    """Keep admission until a thread/process future actually completes."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Starlette uses level cancellation: shield that scope as well as the
        # asyncio task, so waiting here does not become a busy cancellation loop.
        with CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
        if not task.cancelled():
            task.exception()  # Consume any worker error; cancellation takes precedence.
        raise
