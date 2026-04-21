from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """
    Platform-independent GUID type.
    - PostgreSQL: native UUID
    - Other DBs: CHAR(36)
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, UUID):
            return str(value)
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return str(value)


class Base(DeclarativeBase):
    pass


class UserEntity(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    companies: Mapped[list["CompanyEntity"]] = relationship(back_populates="user")


class CompanyEntity(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    industry: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["UserEntity"] = relationship(back_populates="companies")
    jobs: Mapped[list["JobEntity"]] = relationship(back_populates="company")


class JobEntity(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skills_required: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    experience_level: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    compensation: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    structured_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    work_authorization: Mapped[str] = mapped_column(String(64), nullable=False, default="required")
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    company: Mapped["CompanyEntity"] = relationship(back_populates="jobs")
    interviews: Mapped[list["InterviewEntity"]] = relationship(back_populates="job")
    candidate_profiles: Mapped[list["CandidateProfileEntity"]] = relationship(back_populates="job")
    scoring_profile: Mapped["ScoringProfileEntity | None"] = relationship(back_populates="job", uselist=False)
    feedback_items: Mapped[list["CandidateFeedbackEntity"]] = relationship(back_populates="job")
    ats_exports: Mapped[list["ATSExportEntity"]] = relationship(back_populates="job")
    outreach_events: Mapped[list["OutreachEventEntity"]] = relationship(back_populates="job")


class InterviewEntity(Base):
    __tablename__ = "interviews"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_interviews_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="shortlisted")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="interviews")


class CandidateProfileEntity(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_candidate_profiles_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision: Mapped[str] = mapped_column(String(64), nullable=False, default="weak")
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="LOW")
    last_scored_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="candidate_profiles")


class ScoringProfileEntity(Base):
    __tablename__ = "scoring_profiles"
    __table_args__ = (UniqueConstraint("job_id", name="uq_scoring_profiles_job"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    weight_pdl: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    weight_semantic: Mapped[float] = mapped_column(Float, nullable=False, default=0.45)
    weight_skill: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    weight_recency: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    feedback_bias: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    elite_reasoning_bonus: Mapped[float] = mapped_column(Float, nullable=False, default=0.08)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="scoring_profile")


class CandidateFeedbackEntity(Base):
    __tablename__ = "candidate_feedback"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_candidate_feedback_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    feedback: Mapped[str] = mapped_column(String(16), nullable=False)  # accept | reject
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="feedback_items")


class ATSExportEntity(Base):
    __tablename__ = "ats_exports"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="merge")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    response_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="ats_exports")


class OutreachEventEntity(Base):
    __tablename__ = "outreach_events"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_outreach_events_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="sendgrid")
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="outreach_events")
