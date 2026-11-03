from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


TEST_DATABASE_NAME = "efhaes_wedding_test"


def require_test_database_url(database_url: str) -> str:
    try:
        database_name = make_url(database_url).database
    except ArgumentError:
        raise RuntimeError(
            "Invalid database URL; refusing to run against an unverified database"
        ) from None

    if database_name != TEST_DATABASE_NAME:
        raise RuntimeError(
            f"Database URL must target {TEST_DATABASE_NAME}; refusing database access"
        )

    return database_url


def require_test_database_connection(connection: Connection) -> None:
    try:
        database_name = connection.scalar(text("SELECT current_database()"))
    finally:
        connection.rollback()

    if database_name != TEST_DATABASE_NAME:
        raise RuntimeError(
            f"PostgreSQL connection must target {TEST_DATABASE_NAME}; refusing database access"
        )
