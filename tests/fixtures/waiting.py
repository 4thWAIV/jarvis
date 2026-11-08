"""Waiting on a condition instead of guessing how long it takes.

A test that sleeps a fixed time and then asserts on what a background task,
thread or page has done guesses how fast the machine is, and a busy CI runner
guesses wrong. These wait for the condition itself: they return as soon as it
holds, and give up only after a limit long enough for any runner (LIMIT_SEC).
Each returns the predicate's last value, so `assert until(...)` reads as the
assert it replaces.
"""
import asyncio
import time

LIMIT_SEC = 15.0
STEP_SEC = 0.01


def until(predicate, timeout: float = LIMIT_SEC, step: float = STEP_SEC):
    """For a thread or another process: poll `predicate` until it is truthy."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)


async def until_async(predicate, timeout: float = LIMIT_SEC, step: float = STEP_SEC):
    """On the event loop: poll `predicate` until it is truthy, letting the
    loop's other tasks run between looks. `predicate` may be a coroutine
    function."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(step)
