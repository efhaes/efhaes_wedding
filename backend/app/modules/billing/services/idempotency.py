import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.billing_idempotency_record import BillingIdempotencyRecord


_KEY_PATTERN = re.compile(r"[^\x00-\x20\x7f]{16,255}\Z")


def hash_idempotency_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key must be 16 to 255 visible characters",
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def request_fingerprint(operation: str, payload: dict[str, str]) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def claim_idempotency_record(
    session: Session,
    *,
    user_id: UUID,
    operation: str,
    key_hash: str,
    fingerprint: str,
) -> tuple[BillingIdempotencyRecord, bool]:
    values = {
        "id": uuid4(),
        "user_id": user_id,
        "operation": operation,
        "key_hash": key_hash,
        "request_fingerprint": fingerprint,
        "status": "in_progress",
    }
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = postgresql_insert(BillingIdempotencyRecord).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["user_id", "operation", "key_hash"]
        )
    elif dialect_name == "sqlite":
        statement = sqlite_insert(BillingIdempotencyRecord).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["user_id", "operation", "key_hash"]
        )
    else:
        raise RuntimeError(
            f"Idempotency claim is unsupported for database dialect {dialect_name}"
        )

    result = session.execute(statement)
    claimed = result.rowcount == 1
    record = session.scalar(
        select(BillingIdempotencyRecord).where(
            BillingIdempotencyRecord.user_id == user_id,
            BillingIdempotencyRecord.operation == operation,
            BillingIdempotencyRecord.key_hash == key_hash,
        )
    )
    if record is None:
        raise RuntimeError("Idempotency claim did not produce a persistent record")
    if not claimed and record.request_fingerprint != fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different request",
        )
    if not claimed and record.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotent operation is still in progress",
            headers={"Retry-After": "1"},
        )
    return record, claimed


def complete_idempotency_record(
    record: BillingIdempotencyRecord,
    *,
    personal_purchase_id: UUID | None = None,
    subscription_id: UUID | None = None,
    payment_id: UUID | None = None,
    checkout_attempt_id: UUID | None = None,
) -> None:
    result_count = sum(
        value is not None
        for value in (
            personal_purchase_id,
            subscription_id,
            checkout_attempt_id,
        )
    )
    if result_count != 1:
        raise ValueError("An idempotency result must reference exactly one resource")
    has_transaction = (
        personal_purchase_id is not None or subscription_id is not None
    )
    if has_transaction != (payment_id is not None):
        raise ValueError("Purchase and subscription results must include their payment")
    if checkout_attempt_id is not None and (
        payment_id is not None
        or personal_purchase_id is not None
        or subscription_id is not None
    ):
        raise ValueError("Checkout idempotency results must reference the attempt only")

    record.personal_purchase_id = personal_purchase_id
    record.subscription_id = subscription_id
    record.payment_id = payment_id
    record.checkout_attempt_id = checkout_attempt_id
    record.status = "completed"
    record.completed_at = datetime.now(timezone.utc)


def mark_idempotency_replayed(record: BillingIdempotencyRecord) -> None:
    record.replay_count += 1
    record.last_replayed_at = datetime.now(timezone.utc)
