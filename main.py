import asyncio
from agent.engine import start_agent
from agent.crash_handler import CrashRecorder

crash_recorder = CrashRecorder()
crash_recorder.install()


if __name__ == "__main__":
    try:
        asyncio.run(start_agent())
    except KeyboardInterrupt:
        pass