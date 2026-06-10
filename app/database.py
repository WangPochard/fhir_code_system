from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager

try:
    from .config import get_settings
except ImportError:
    from config import get_settings

settings = get_settings()

# ==================================================================
# SNOMED CT Database
# ==================================================================
snomed_engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SnomedSession = sessionmaker(autocommit=False, autoflush=False, bind=snomed_engine)
SnomedBase = declarative_base()

# ==================================================================
# ICD-10 PCS Database
# ==================================================================
icd10_engine = create_engine(
    settings.icd10_database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
ICD10Session = sessionmaker(autocommit=False, autoflush=False, bind=icd10_engine)
ICD10Base = declarative_base()

# ==================================================================
# LOINC Database
# ==================================================================
loinc_engine = create_engine(
    settings.loinc_database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
LOINCSession = sessionmaker(autocommit=False, autoflush=False, bind=loinc_engine)
LOINCBase = declarative_base()

# ==================================================================
# 向下相容（原本的 SessionLocal / Base 指向 SNOMED CT）
# ==================================================================
SessionLocal = SnomedSession
Base = SnomedBase

# ==================================================================
# FastAPI Depends
# ==================================================================
def get_snomed_db():
    db = SnomedSession()
    try:
        yield db
    finally:
        db.close()

def get_icd10_db():
    db = ICD10Session()
    try:
        yield db
    finally:
        db.close()

def get_loinc_db():
    db = LOINCSession()
    try:
        yield db
    finally:
        db.close()

# 向下相容
get_db = get_snomed_db

# ==================================================================
# Context Managers
# ==================================================================
@contextmanager
def get_snomed_context():
    db = SnomedSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@contextmanager
def get_icd10_context():
    db = ICD10Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@contextmanager
def get_loinc_context():
    db = LOINCSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# 向下相容
get_db_context = get_snomed_context
