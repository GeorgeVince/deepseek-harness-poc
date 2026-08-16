#!/usr/bin/env python3
"""Credential-free command runner for the isolated sandbox sidecar."""

import json
import os
import signal
import socketserver
import subprocess
import tempfile
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("SANDBOX_SOCKET", "/run/sandbox/runner.sock"))
WORKSPACE = Path("/workspace")
MAX_REQUEST_BYTES = 20_000
MAX_OUTPUT_BYTES = 32_000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 30


def _tail(stream) -> tuple[str, bool]:
    size = stream.tell()
    stream.seek(max(0, size - MAX_OUTPUT_BYTES))
    return stream.read().decode("utf-8", errors="replace"), size > MAX_OUTPUT_BYTES


def execute(kind: str, code: str, timeout: int) -> dict:
    if kind not in {"bash", "python"}:
        raise ValueError("kind must be bash or python")
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_REQUEST_BYTES:
        raise ValueError("code must contain 1 to 20000 bytes")
    if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be between 1 and 30 seconds")

    command = (
        ["bash", "--noprofile", "--norc", "-c", code]
        if kind == "bash"
        else ["python", "-I", "-c", code]
    )
    argv = [
        "bwrap",
        "--unshare-all",
        "--new-session",
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", str(WORKSPACE), str(WORKSPACE),
        "--tmpfs", "/run/sandbox",
        "--chdir", str(WORKSPACE),
        "--",
        "prlimit",
        "--core=0",
        f"--fsize={MAX_FILE_BYTES}",
        "--nofile=64",
        f"--cpu={timeout}:{timeout + 1}",
        "--",
        *command,
    ]
    env = {
        "HOME": str(WORKSPACE),
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "TMPDIR": "/tmp",
    }

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=WORKSPACE,
            env=env,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

        output, output_truncated = _tail(stdout)
        error, error_truncated = _tail(stderr)
        return {
            "stdout": output,
            "stderr": error,
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "output_truncated": output_truncated or error_truncated,
        }


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("request is too large")
            request = json.loads(raw)
            if request.get("kind") == "health":
                response = {"ok": True}
            else:
                response = execute(
                    request.get("kind"),
                    request.get("code"),
                    request.get("timeout", MAX_TIMEOUT_SECONDS),
                )
        except Exception as error:
            response = {"error": str(error)}
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")


def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(str(SOCKET_PATH), Handler) as server:
        SOCKET_PATH.chmod(0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
