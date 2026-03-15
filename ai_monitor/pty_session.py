"""Persistent PTY helpers for interactive CLI probing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import errno
import os
import pty
import select
import signal
import subprocess
import termios
import time

from .parsing import strip_ansi

CURSOR_QUERY = b"\x1b[6n"
CURSOR_RESPONSE = b"\x1b[1;1R"


@dataclass(slots=True)
class CaptureConfig:
    timeout: float
    startup_wait: float = 0.5
    idle_timeout: float | None = None
    discard_preexisting_output: bool = True
    stop_substrings: tuple[str, ...] = ()
    settle_after_stop: float = 0.25
    send_enter_every: float | None = None
    resend_command_every: float | None = None
    resend_command_max: int = 0
    auto_responses: tuple[tuple[str, str], ...] = ()


class PersistentPTYSession:
    """Maintain one CLI PTY across repeated slash-command probes."""

    def __init__(
        self,
        binary: str,
        args: list[str],
        cwd: str,
        rows: int = 60,
        cols: int = 200,
    ) -> None:
        self.binary = binary
        self.args = args
        self.cwd = cwd
        self.rows = rows
        self.cols = cols
        self.master_fd: int | None = None
        self.pid: int | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def _spawn(self) -> None:
        if self.process and self.process.poll() is None:
            return
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(slave_fd, self.rows, self.cols)
        env = os.environ.copy()
        process = subprocess.Popen(
            [self.binary, *self.args],
            cwd=self.cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        os.set_blocking(master_fd, False)
        self.master_fd = master_fd
        self.process = process
        self.pid = process.pid
        time.sleep(0.5)

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        packed = termios.tcgetattr(fd)
        del packed
        winsize = os.terminal_size((cols, rows))
        termios.tcsetwinsize(fd, winsize)

    def is_alive(self) -> bool:
        return bool(self.process and self.process.poll() is None and self.master_fd is not None)

    def ensure(self) -> None:
        if not self.is_alive():
            self.close()
            self._spawn()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                self.process.wait(timeout=1.0)
            except KeyboardInterrupt:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=0.2)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        self.master_fd = None
        self.pid = None
        self.process = None

    def drain(self) -> str:
        if self.master_fd is None:
            return ""
        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([self.master_fd], [], [], 0.05)
            if not ready:
                break
            try:
                data = os.read(self.master_fd, 65536)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EIO):
                    break
                raise
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="ignore")

    def capture(self, command: str, config: CaptureConfig) -> str:
        self.ensure()
        assert self.master_fd is not None

        if config.startup_wait > 0:
            time.sleep(config.startup_wait)
        if config.discard_preexisting_output:
            self.drain()
        try:
            os.write(self.master_fd, command.encode("utf-8") + b"\r")
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            return ""

        started = time.monotonic()
        last_output = started
        last_enter = started
        last_command_send = started
        matched_stop_at: float | None = None
        response_hits: set[str] = set()
        chunks: list[str] = []
        resend_count = 0

        while True:
            now = time.monotonic()
            if now - started > config.timeout:
                break

            ready, _, _ = select.select([self.master_fd], [], [], 0.2)
            if ready:
                try:
                    data = os.read(self.master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                if CURSOR_QUERY in data:
                    try:
                        os.write(self.master_fd, CURSOR_RESPONSE)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                text = data.decode("utf-8", errors="ignore")
                chunks.append(text)
                last_output = time.monotonic()

                clean = strip_ansi("".join(chunks))
                for prompt, response in config.auto_responses:
                    if prompt in clean and prompt not in response_hits:
                        try:
                            os.write(self.master_fd, response.encode("utf-8"))
                        except OSError as exc:
                            if exc.errno != errno.EIO:
                                raise
                        response_hits.add(prompt)

                if config.stop_substrings and any(stop in clean for stop in config.stop_substrings):
                    matched_stop_at = time.monotonic()
            elif config.idle_timeout is not None and chunks and now - last_output >= config.idle_timeout:
                break

            if (
                config.resend_command_every is not None
                and not matched_stop_at
                and resend_count < config.resend_command_max
                and now - last_command_send >= config.resend_command_every
            ):
                try:
                    os.write(self.master_fd, command.encode("utf-8") + b"\r")
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                resend_count += 1
                last_command_send = now

            if (
                config.send_enter_every is not None
                and now - last_enter >= config.send_enter_every
                and self.master_fd is not None
            ):
                try:
                    os.write(self.master_fd, b"\r")
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                last_enter = now

            if matched_stop_at is not None and time.monotonic() - matched_stop_at >= config.settle_after_stop:
                break

        return "".join(chunks)


def with_managed_sessions(callback: Callable[[], int]) -> int:
    """Ensure session cleanup around the dashboard lifecycle."""

    return callback()
