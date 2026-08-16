import json
import os
import socket
from pathlib import Path

SOCKET_PATH = os.environ.get("SANDBOX_SOCKET", "/run/sandbox/runner.sock")


def call(kind: str, code: str, timeout: int = 5) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout + 5)
        client.connect(SOCKET_PATH)
        client.sendall(json.dumps({"kind": kind, "code": code, "timeout": timeout}).encode() + b"\n")
        response = client.makefile("rb").readline(70_001)
    return json.loads(response)


def test_runner_isolates_code_from_the_chatbot() -> None:
    result = call(
        "python",
        """import glob,json,os,socket
visible = b''
for path in glob.glob('/proc/[0-9]*/environ'):
    try:
        visible += open(path, 'rb').read()
    except OSError:
        pass
network = socket.socket().connect_ex(('1.1.1.1', 80)) == 0
print(json.dumps({'env': dict(os.environ), 'visible': visible.decode(errors='ignore'), 'network': network, 'runner_socket': os.path.exists('/run/sandbox/runner.sock')}))
""",
    )
    facts = json.loads(result["stdout"])
    assert result["exit_code"] == 0
    assert "OPENAI_TOKEN" not in facts["visible"]
    assert "OPENAI_API_KEY" not in facts["visible"]
    assert "DATABASE_URL" not in facts["visible"]
    assert facts["network"] is False
    assert facts["runner_socket"] is False

    result = call("bash", "printf isolated > sidecar-check.txt")
    assert result["exit_code"] == 0
    assert Path("/workspace/sidecar-check.txt").read_text() == "isolated"

    result = call("python", "import time; time.sleep(5)", timeout=1)
    assert result["timed_out"] is True
