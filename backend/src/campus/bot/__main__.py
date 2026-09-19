"""Run the singleton MAX bot with ``python -m campus.bot``."""

import asyncio

from campus.bot.runtime import run

if __name__ == "__main__":
    asyncio.run(run())
