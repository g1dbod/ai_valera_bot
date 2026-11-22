from __future__ import annotations

import logging
import time
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    BigInteger, String, Text, Boolean, ForeignKey,
    UniqueConstraint, select, delete
)
from typing import Optional, List

from bot.config import DATABASE_URL

log = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UserAPI(Base):
    __tablename__ = "user_apis"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)

    models: Mapped[List["UserModel"]] = relationship(
        back_populates="api", cascade="all, delete-orphan"
    )


class UserModel(Base):
    __tablename__ = "user_models"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    api_id: Mapped[int] = mapped_column(ForeignKey("user_apis.id", ondelete="CASCADE"))
    model_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat"
    )  # chat | image | svg | styled
    style_image_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    api: Mapped["UserAPI"] = relationship(back_populates="models")


class UserContext(Base):
    __tablename__ = "user_contexts"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("user_models.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables ensured.")


# ── API helpers ──────────────────────────────────────────────────────────────

async def add_api(
    session: AsyncSession, user_id: int, name: str, api_key: str, base_url: str
) -> UserAPI:
    obj = UserAPI(user_id=user_id, name=name, api_key=api_key, base_url=base_url)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def get_apis(session: AsyncSession, user_id: int) -> List[UserAPI]:
    result = await session.execute(
        select(UserAPI).where(UserAPI.user_id == user_id)
    )
    return list(result.scalars().all())


async def get_api_by_name(
    session: AsyncSession, user_id: int, name: str
) -> Optional[UserAPI]:
    result = await session.execute(
        select(UserAPI).where(UserAPI.user_id == user_id, UserAPI.name == name)
    )
    return result.scalar_one_or_none()


async def delete_api(session: AsyncSession, user_id: int, name: str) -> bool:
    result = await session.execute(
        delete(UserAPI)
        .where(UserAPI.user_id == user_id, UserAPI.name == name)
        .returning(UserAPI.id)
    )
    await session.commit()
    return result.fetchone() is not None


# ── Model helpers ────────────────────────────────────────────────────────────

async def add_model(
    session: AsyncSession,
    user_id: int,
    name: str,
    model_id: str,
    api_id: int,
    model_type: str = "chat",
    style_image_file_id: Optional[str] = None,
) -> UserModel:
    obj = UserModel(
        user_id=user_id,
        name=name,
        model_id=model_id,
        api_id=api_id,
        model_type=model_type,
        style_image_file_id=style_image_file_id,
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def get_models(session: AsyncSession, user_id: int) -> List[UserModel]:
    result = await session.execute(
        select(UserModel)
        .where(UserModel.user_id == user_id)
        .order_by(UserModel.name)
    )
    return list(result.scalars().all())


async def get_model_by_name(
    session: AsyncSession, user_id: int, name: str
) -> Optional[UserModel]:
    result = await session.execute(
        select(UserModel).where(UserModel.user_id == user_id, UserModel.name == name)
    )
    return result.scalar_one_or_none()


async def get_active_model(
    session: AsyncSession, user_id: int
) -> Optional[UserModel]:
    result = await session.execute(
        select(UserModel).where(
            UserModel.user_id == user_id, UserModel.is_active == True  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def set_active_model(
    session: AsyncSession, user_id: int, name: str
) -> Optional[UserModel]:
    # deactivate all
    models = await get_models(session, user_id)
    for m in models:
        m.is_active = False
    # activate target
    target = await get_model_by_name(session, user_id, name)
    if target is None:
        await session.commit()
        return None
    target.is_active = True
    await session.commit()
    await session.refresh(target)
    return target


async def delete_model(session: AsyncSession, user_id: int, name: str) -> bool:
    result = await session.execute(
        delete(UserModel)
        .where(UserModel.user_id == user_id, UserModel.name == name)
        .returning(UserModel.id)
    )
    await session.commit()
    return result.fetchone() is not None


# ── Context helpers ──────────────────────────────────────────────────────────

async def get_context(session: AsyncSession, user_id: int) -> str:
    result = await session.execute(
        select(UserContext).where(UserContext.user_id == user_id)
    )
    obj = result.scalar_one_or_none()
    return obj.context if obj else ""


async def set_context(session: AsyncSession, user_id: int, context: str) -> None:
    result = await session.execute(
        select(UserContext).where(UserContext.user_id == user_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        obj = UserContext(user_id=user_id, context=context)
        session.add(obj)
    else:
        obj.context = context
    await session.commit()


async def clear_context(session: AsyncSession, user_id: int) -> None:
    await set_context(session, user_id, "")


async def append_history(
    session: AsyncSession,
    user_id: int,
    model_db_id: int,
    role: str,
    content: str,
) -> None:
    session.add(ChatHistory(
        user_id=user_id,
        model_id=model_db_id,
        role=role,
        content=content,
        created_at=time.time(),
    ))
    await session.commit()


async def get_history(
    session: AsyncSession,
    user_id: int,
    model_db_id: int,
    limit: int = 40,
) -> list[dict]:
    result = await session.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id, ChatHistory.model_id == model_db_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
    )
    rows = list(reversed(result.scalars().all()))
    return [{"role": r.role, "content": r.content} for r in rows]


async def clear_history(
    session: AsyncSession,
    user_id: int,
    model_db_id: int,
) -> None:
    await session.execute(
        delete(ChatHistory).where(
            ChatHistory.user_id == user_id,
            ChatHistory.model_id == model_db_id,
        )
    )
    await session.commit()