from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
import os

# Use DATABASE_URL (for Supabase/Postgres) if provided, otherwise fallback to local sqlite cache
DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('CACHE_DB_URL') or 'sqlite:///cache.db'

# If using Postgres on platforms like Supabase, ensure SSL requirement is preserved
if DATABASE_URL.startswith('postgres://'):
    # SQLAlchemy prefers postgresql+psycopg
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg2://', 1)

connect_args = {}
if DATABASE_URL.startswith('sqlite'):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class HeadToHeadCache(Base):
    __tablename__ = 'head_to_head_cache'
    id = Column(Integer, primary_key=True, index=True)
    driver1_id = Column(Integer, index=True)
    driver2_id = Column(Integer, index=True)
    season = Column(Integer, index=True, nullable=True)
    mode = Column(String(32), default='season')
    response_json = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class RaceTelemetry(Base):
    __tablename__ = 'race_telemetry'
    id = Column(Integer, primary_key=True, index=True)
    race_id = Column(Integer, index=True)
    payload = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
