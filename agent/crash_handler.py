import sys
import os
import traceback
import asyncio
import re
from datetime import datetime
from collections import deque

ANSI_ESCAPE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\]8;.*?\x1b\\)')
SHARED_LOG_PATH = os.path.join("crash_reports", "shared_live_logs.log")
MAX_SHARED_LINES = 200

_STREAMS_WRAPPED = False


def _append_shared_line(line: str):
    """Append one line to the shared log, keeping only the last MAX_SHARED_LINES."""
    try:
        os.makedirs("crash_reports", exist_ok=True)
        lines = []
        if os.path.exists(SHARED_LOG_PATH):
            with open(SHARED_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        lines.append(line + "\n")
        with open(SHARED_LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_SHARED_LINES:])
    except Exception:
        pass


class _StreamInterceptor:
    """Mirrors writes to an in-memory buffer and to a shared log file."""

    def __init__(self, original_stream, buffer: deque):
        self._orig = original_stream
        self._buffer = buffer

    @property
    def buffer(self):
        return getattr(self._orig, "buffer", self._orig)

    def write(self, message: str):
        self._orig.write(message)
        clean_text = ANSI_ESCAPE.sub('', message).strip()

        if clean_text:
            t_stamp = datetime.now().strftime("%H:%M:%S")
            formatted_log = f"[{t_stamp}] {clean_text}"

            self._buffer.append(formatted_log)
            _append_shared_line(formatted_log)

    def flush(self):
        self._orig.flush()

    def fileno(self):
        return self._orig.fileno()

    def isatty(self):
        return self._orig.isatty()


class CrashRecorder:
    def __init__(self, max_logs: int = 100, reports_dir: str = "crash_reports"):
        self.buffer = deque(maxlen=max_logs)
        self.reports_dir = reports_dir
        os.makedirs(self.reports_dir, exist_ok=True)
        self.process_name = os.path.basename(sys.argv[0]) if sys.argv else "Background Process"

    def install(self, loop: asyncio.AbstractEventLoop | None = None):
        global _STREAMS_WRAPPED

        if not _STREAMS_WRAPPED:
            sys.stdout = _StreamInterceptor(sys.stdout, self.buffer)
            sys.stderr = _StreamInterceptor(sys.stderr, self.buffer)
            sys.excepthook = self._handle_crash
            _STREAMS_WRAPPED = True

        if loop:
            def async_handler(loop, context):
                exc = context.get("exception")
                if exc:
                    self._handle_crash(type(exc), exc, exc.__traceback__)
                else:
                    self.buffer.append(f"[ASYNC ERROR]: {context.get('message')}")
                loop.default_exception_handler(context)

            loop.set_exception_handler(async_handler)

    def _handle_crash(self, exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return

        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename_safe_process = self.process_name.replace('.py', '')
        filepath = os.path.join(self.reports_dir, f"crash_{filename_safe_process}_{timestamp_str}.txt")

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        logs_to_write = list(self.buffer)
        if os.path.exists(SHARED_LOG_PATH):
            try:
                with open(SHARED_LOG_PATH, "r", encoding="utf-8") as f:
                    logs_to_write = [line.strip() for line in f.readlines()[-100:]]
            except Exception:
                pass

        report = [
            f"Crash Report - {datetime.now().isoformat()}",
            f"Failing Process: {self.process_name}",
            "\nCrash Reason / Exception:",
            f"{exc_type.__name__}: {exc_value}\n",
            "Full Traceback:",
            tb_text,
            "Last Global Logs Prior To Crash (From All Services):",
            *logs_to_write,
        ]

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(report))
            sys.__stderr__.write(
                f"\n[CRITICAL]: '{self.process_name}' crashed! Diagnostic report saved to: {filepath}\n"
            )
            self._rotate_crash_reports()
        except Exception as err:
            sys.__stderr__.write(f"\n[CRITICAL]: Failed to save crash report for {self.process_name}: {err}\n")

    def _rotate_crash_reports(self, keep: int = 10):
        """Keep only the most recent `keep` crash_*.txt files; delete the rest."""
        try:
            reports = sorted(
                [f for f in os.listdir(self.reports_dir) if f.startswith("crash_") and f.endswith(".txt")],
                reverse=True,
            )
            for old_file in reports[keep:]:
                os.remove(os.path.join(self.reports_dir, old_file))
        except Exception:
            pass

    def create_log_report(self):
        timestamp_str = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(self.reports_dir, f"log_report_{timestamp_str}.txt")

        logs_to_write = list(self.buffer)
        if os.path.exists(SHARED_LOG_PATH):
            try:
                with open(SHARED_LOG_PATH, "r", encoding="utf-8") as f:
                    logs_to_write = [line.strip() for line in f.readlines()[-100:]]
            except Exception:
                pass

        report = [
            f"Log Report - {datetime.now()}",
            "Last 100 Global Logs:",
            *logs_to_write,
        ]

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(report))
            return f"\n[INFO]: Log report saved to: {filepath}\n"
        except Exception as err:
            return f"\n[CRITICAL]: Failed to save log report: {err}\n"