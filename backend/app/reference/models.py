"""reference schema: universities, campuses, majors."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

SCHEMA = "reference"


class University(Base, TimestampMixin):
    """A degree-granting institution (not a campus)."""

    __tablename__ = "universities"
    __table_args__ = (
        UniqueConstraint("ministry_code", name="uq_universities_ministry_code"),
        CheckConstraint("length(name) > 0", name="name_not_blank"),
        CheckConstraint("length(province_code) = 2", name="province_code_len"),
        Index("ix_universities_province_city", "province_code", "city_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 教育部院校代码 / 省编代码; kept as text to preserve leading zeros
    ministry_code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64))
    province_code: Mapped[str] = mapped_column(String(2), nullable=False)
    province_name: Mapped[str] = mapped_column(String(32), nullable=False)
    city_code: Mapped[str | None] = mapped_column(String(8))
    city_name: Mapped[str | None] = mapped_column(String(32))
    # School characteristics used by the ACADEMIC preference dimension
    is_985: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_211: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_double_first_class: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_public: Mapped[bool | None] = mapped_column(Boolean)
    website: Mapped[str | None] = mapped_column(String(255))
    remark: Mapped[str | None] = mapped_column(Text)

    campuses: Mapped[list[Campus]] = relationship(back_populates="university", cascade="all, delete-orphan")


class Campus(Base, TimestampMixin):
    """A physical campus; 校区 matters because plans and addresses differ."""

    __tablename__ = "campuses"
    __table_args__ = (
        UniqueConstraint("university_id", "name", name="uq_campuses_university_id_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.universities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    province_code: Mapped[str | None] = mapped_column(String(2))
    city_code: Mapped[str | None] = mapped_column(String(8))
    city_name: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))

    university: Mapped[University] = relationship(back_populates="campuses")


class Major(Base, TimestampMixin):
    """教育部专业（专业类）标准库条目。

    `code` follows the 教育部 专业目录: 4 digits for 专业类, 6 digits for 专业.
    """

    __tablename__ = "majors"
    __table_args__ = (
        UniqueConstraint("code", name="uq_majors_code"),
        CheckConstraint("level in ('MAJOR_CLASS','MAJOR')", name="level_valid"),
        Index("ix_majors_discipline_code", "discipline_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    discipline_code: Mapped[str | None] = mapped_column(String(2))
    discipline_name: Mapped[str | None] = mapped_column(String(32))
    category_code: Mapped[str | None] = mapped_column(String(4))
    category_name: Mapped[str | None] = mapped_column(String(64))
    degree_awarded: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
