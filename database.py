import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, JSON, select
from config import DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME

# Формируем URL для подключения к внешней БД
DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

Base = declarative_base()

class UserConfig(Base):
    __tablename__ = "user_configs"
    user_id = Column(Integer, primary_key=True) 
    apis = Column(JSON, default={})        # { "Name": {"key": "...", "url": "..."} }
    models = Column(JSON, default={})      # { "Name": ["model1", "model2"] }
    selected_api = Column(String, nullable=True)
    selected_model = Column(String, nullable=True)
    context = Column(Text, default="You are a helpful assistant.")
    history = Column(JSON, default=[])

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(bind=sync_conn))

async def get_config(user_id):
    async with AsyncSessionLocal() as session:
        stmt = select(UserConfig).where(UserConfig.user_id == user_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            config = UserConfig(user_id=user_id, apis={}, models={}, history=[])
            session.add(config)
            await session.commit()
            await session.refresh(config)
        return config

async def save_config(config):
    async with AsyncSessionLocal() as session:
        await session.merge(config)
        await session.commit()
