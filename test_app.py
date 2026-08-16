import json
from pathlib import Path

import pytest

from app import parse_chat_request, read_pi_oauth


def test_read_pi_oauth_accepts_only_current_tokens(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"openai-codex": {"type": "oauth", "access": "token", "expires": 1_000_000}}))
    assert read_pi_oauth(auth, 0) == "token"
    with pytest.raises(RuntimeError, match="expired or expiring"):
        read_pi_oauth(auth, 800_000)


def test_parse_chat_request_validates_input() -> None:
    assert parse_chat_request(b'{"message":" hi ","session_id":"abc-123"}') == ("hi", "abc-123")
    with pytest.raises(ValueError, match="session_id is invalid"):
        parse_chat_request(b'{"message":"hi","session_id":"../escape"}')
