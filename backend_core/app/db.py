import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Prefer explicit DATABASE_URL env var. If not provided, construct a safe
# local SQLite URL in a `data/` folder adjacent to the package directory.
env_db = os.getenv("DATABASE_URL")
if env_db:
    DATABASE_URL = env_db
else:
    pkg_root = Path(__file__).resolve().parents[1]
    data_dir = pkg_root / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort; if creating fails fall back to in-memory DB
        data_dir = None
    if data_dir:
        db_file = data_dir / "kumar_core.db"
        # Use POSIX path style for SQLAlchemy URL on Windows as well
        DATABASE_URL = f"sqlite:///{db_file.as_posix()}"
    else:
        DATABASE_URL = "sqlite:///:memory:"

print(f"[backend_core] Using DATABASE_URL: {DATABASE_URL}")

# Use echo=False in production; set echo=True for debugging
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def create_db_and_tables():
    from sqlalchemy import inspect
    Base.metadata.create_all(bind=engine)
