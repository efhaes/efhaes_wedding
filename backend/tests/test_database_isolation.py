from unittest.mock import Mock

import pytest
from sqlalchemy.engine import Connection

from app.database.isolation import (
    require_test_database_connection,
    require_test_database_url,
)


def test_database_url_guard_accepts_only_test_database():
    test_url = "postgresql+psycopg://localhost/efhaes_wedding_test"

    assert require_test_database_url(test_url) == test_url


def test_database_url_guard_rejects_main_database():
    with pytest.raises(RuntimeError, match="must target efhaes_wedding_test"):
        require_test_database_url(
            "postgresql+psycopg://localhost/efhaes_wedding"
        )


def test_database_url_guard_rejects_invalid_url():
    with pytest.raises(RuntimeError, match="Invalid database URL"):
        require_test_database_url("not a database URL")


def test_database_connection_guard_rejects_non_postgresql_database():
    connection = Mock(spec=Connection)
    connection.scalar.return_value = "efhaes_wedding"

    with pytest.raises(RuntimeError, match="must target efhaes_wedding_test"):
        require_test_database_connection(connection)

    connection.scalar.assert_called_once()


def test_database_connection_guard_rolls_back_implicit_transaction():
    transaction = {"active": False}
    connection = Mock(spec=Connection)

    def scalar(_statement):
        transaction["active"] = True
        return "efhaes_wedding_test"

    connection.scalar.side_effect = scalar
    connection.rollback.side_effect = lambda: transaction.update(active=False)
    connection.in_transaction.side_effect = lambda: transaction["active"]

    require_test_database_connection(connection)

    assert connection.in_transaction() is False
    connection.rollback.assert_called_once_with()
