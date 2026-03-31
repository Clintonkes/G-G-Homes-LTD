"""SQLAlchemy declarative base used by every ORM model in the project."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
