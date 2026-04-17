import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Railway sometimes gives postgres:// — SQLAlchemy needs postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Models ──────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id         = Column(Integer, primary_key=True)
    email      = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role       = Column(String, nullable=False, default="viewer")  # admin | viewer
    created_at = Column(DateTime, default=datetime.utcnow)


class Designer(Base):
    __tablename__ = "designers"
    id         = Column(Integer, primary_key=True)
    name       = Column(String, unique=True, nullable=False)   # e.g. "Kasey/Asije"
    label      = Column(String, nullable=False)                # e.g. "Kasey / Asije"
    color_hex  = Column(String, nullable=False, default="#888888")
    active     = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    clients  = relationship("Client", back_populates="designer")


class Client(Base):
    __tablename__ = "clients"
    id              = Column(Integer, primary_key=True)
    name            = Column(String, nullable=False, unique=True)
    name_normalized = Column(String, nullable=False, index=True)  # trimmed + upper
    designer_id     = Column(Integer, ForeignKey("designers.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    designer = relationship("Designer", back_populates="clients")
    projects = relationship("Project", back_populates="client")


class Period(Base):
    __tablename__ = "periods"
    id          = Column(Integer, primary_key=True)
    label       = Column(String, nullable=False)          # e.g. "Q3 2024" or filename
    filename    = Column(String, nullable=False)
    row_count   = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    is_duplicate = Column(Boolean, default=False)

    projects = relationship("Project", back_populates="period")
    uploader = relationship("User")

    __table_args__ = (
        UniqueConstraint("filename", "row_count", name="uq_period_file_rows"),
    )


class Project(Base):
    __tablename__ = "projects"
    id           = Column(Integer, primary_key=True)
    period_id    = Column(Integer, ForeignKey("periods.id"), nullable=False)
    client_id    = Column(Integer, ForeignKey("clients.id"), nullable=False)
    revenue      = Column(Float, default=0)
    profit       = Column(Float, default=0)
    time_billing = Column(Float, default=0)
    margin       = Column(Float, default=0)
    raw_row      = Column(JSONB, nullable=True)   # full source row preserved

    period = relationship("Period", back_populates="projects")
    client = relationship("Client", back_populates="projects")


def create_tables():
    Base.metadata.create_all(bind=engine)
