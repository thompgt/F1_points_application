from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
import os

# Use DATABASE_URL (MySQL via docker-compose, or Supabase/Postgres) if provided,
# otherwise fall back to a local sqlite cache so the app still boots un-configured.
DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('CACHE_DB_URL') or 'sqlite:///cache.db'

# If using Postgres on platforms like Supabase, ensure SSL requirement is preserved
if DATABASE_URL.startswith('postgres://'):
    # requirements.txt installs psycopg3 (psycopg[binary]), not psycopg2
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)

# requirements.txt installs PyMySQL; SQLAlchemy's bare `mysql://` default driver is
# MySQLdb (not installed), so pin the dialect explicitly.
if DATABASE_URL.startswith('mysql://'):
    DATABASE_URL = DATABASE_URL.replace('mysql://', 'mysql+pymysql://', 1)

connect_args = {}
engine_kwargs = {}
if DATABASE_URL.startswith('sqlite'):
    connect_args = {"check_same_thread": False}
else:
    # MySQL closes idle connections after wait_timeout (8h default, much lower in
    # some configs) -- without these a pooled connection resurfaces as an
    # "MySQL server has gone away" error on the next request.
    engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 1800}

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)
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

class Race(Base):
    __tablename__ = 'races_db'
    id = Column(Integer, primary_key=True, index=True)
    raceId = Column(Integer, index=True, unique=True)
    name = Column(String(128))
    year = Column(Integer, index=True)
    round = Column(Integer)
    date = Column(String(32))
    circuitId = Column(Integer)


def store_races(race_list):
    db = SessionLocal()
    try:
        for race in race_list:
            # Check if race already exists
            existing = db.query(Race).filter_by(raceId=race['raceId']).first()
            if not existing:
                db.add(Race(
                    raceId=race['raceId'],
                    name=race.get('name',''),
                    year=race.get('year'),
                    round=race.get('round'),
                    date=race.get('date',''),
                    circuitId=race.get('circuitId')
                ))
        db.commit()
    finally:
        db.close()


class RaceTelemetry(Base):
    __tablename__ = 'race_telemetry'
    id = Column(Integer, primary_key=True, index=True)
    race_id = Column(Integer, index=True)
    payload = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    # races_db previously had no `year` column (a prior bug queried it by
    # `round`, which meant the cache never matched). create_all() won't add
    # columns to an already-existing table, so drop and let it recreate --
    # this table is purely a cache repopulated from the CSVs on demand.
    inspector = inspect(engine)
    if inspector.has_table(Race.__tablename__):
        existing_columns = {col['name'] for col in inspector.get_columns(Race.__tablename__)}
        if 'year' not in existing_columns:
            Race.__table__.drop(bind=engine)

    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
