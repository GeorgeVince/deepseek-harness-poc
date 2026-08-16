import uuid

import sqlalchemy as sa

import database


def test_database_round_trip() -> None:
    created = database.create_session()
    session_id = uuid.UUID(created["id"])

    try:
        assert database.get_session(session_id)["title"] == "New chat"

        database.add_message(session_id, "user", "A useful title")
        database.add_message(session_id, "assistant", "Hello")

        assert database.get_session(session_id)["title"] == "A useful title"
        assert [(message["role"], message["content"]) for message in database.list_messages(session_id)] == [
            ("user", "A useful title"),
            ("assistant", "Hello"),
        ]
        assert created["id"] in {session["id"] for session in database.list_sessions()}
    finally:
        with database.engine().begin() as connection:
            connection.execute(sa.delete(database.sessions).where(database.sessions.c.id == session_id))
