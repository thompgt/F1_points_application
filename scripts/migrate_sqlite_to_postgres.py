"""
Simple script to migrate cache rows from local SQLite `cache.db` to the target DATABASE_URL (Supabase/Postgres).
Usage:
  1. Install requirements and ensure DATABASE_URL points to your Supabase Postgres.
  2. Run: python scripts/migrate_sqlite_to_postgres.py

This script reads from the local SQLite file (cache.db) and inserts into Postgres using same SQLAlchemy models.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import Base, HeadToHeadCache

# Source (local sqlite)
SRC_DB = os.getenv('SRC_DB_URL', 'sqlite:///cache.db')
# Target (Postgres / Supabase)
DST_DB = os.getenv('DATABASE_URL') or os.getenv('CACHE_DB_URL')
if DST_DB and DST_DB.startswith('postgres://'):
    DST_DB = DST_DB.replace('postgres://', 'postgresql+psycopg2://', 1)

if not DST_DB:
    raise SystemExit('Set DATABASE_URL to your Supabase Postgres connection string')

src_engine = create_engine(SRC_DB, connect_args={"check_same_thread": False})
dst_engine = create_engine(DST_DB)

SrcSession = sessionmaker(bind=src_engine)
DstSession = sessionmaker(bind=dst_engine)

# reflect source table
from sqlalchemy import MetaData, Table
meta = MetaData()
meta.reflect(bind=src_engine)
if 'head_to_head_cache' not in meta.tables:
    raise SystemExit('No head_to_head_cache table found in source')

HeadToHead = meta.tables['head_to_head_cache']

# create tables in destination if not exists
Base.metadata.create_all(bind=dst_engine)

src = SrcSession()
dst = DstSession()

rows = src.execute(HeadToHead.select()).fetchall()
print(f'Found {len(rows)} rows to migrate')
for r in rows:
    entry = HeadToHeadCache(
        driver1_id = r['driver1_id'],
        driver2_id = r['driver2_id'],
        season = r['season'],
        mode = r['mode'],
        response_json = r['response_json']
    )
    dst.add(entry)

dst.commit()
print('Migration complete')
src.close()
dst.close()
