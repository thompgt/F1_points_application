from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
import os

DATABASE_URL = os.getenv('CACHE_DB_URL', 'sqlite:///cache.db')

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith('sqlite') else {})
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
