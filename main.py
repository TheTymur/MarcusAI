import asyncio
from agent.engine import start_agent


if __name__ == "__main__":
    try:
        asyncio.run(start_agent())
    except KeyboardInterrupt:
        pass