import uuid

import database


def test_database_round_trip() -> None:
    with database.connect() as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"] == "20260816_0002"
    created = database.create_session()
    session_id = uuid.UUID(created["id"])

    try:
        assert database.get_session(session_id)["title"] == "New chat"

        turn_id = uuid.uuid4()
        database.add_message(session_id, "user", "A useful title", turn_id)
        database.add_tool_call(
            session_id, turn_id, "call-1", "child-session", "get_uk_weather", '{"location":"Cardiff"}'
        )
        database.complete_tool_call(session_id, "call-1", '{"temperature_c":12}', False)
        database.add_message(session_id, "assistant", "Hello", turn_id)

        assert database.get_session(session_id)["title"] == "A useful title"
        messages = database.list_messages(session_id)
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "A useful title"),
            ("assistant", "Hello"),
        ]
        assert messages[1]["tool_calls"] == [{
            "id": "call-1",
            "agent_session_id": "child-session",
            "name": "get_uk_weather",
            "arguments": '{"location":"Cardiff"}',
            "result": '{"temperature_c":12}',
            "is_error": False,
        }]
        assert created["id"] in {session["id"] for session in database.list_sessions()}
    finally:
        with database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
