from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKey


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "platform_role IN ('user', 'admin')", name="ck_users_platform_role"
        ),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    platform_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default="user"
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")
    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", foreign_keys="WorkspaceMember.user_id"
    )
