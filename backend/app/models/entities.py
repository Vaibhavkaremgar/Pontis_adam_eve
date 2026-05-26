from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserEntity(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="recruiter")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    companies: Mapped[list["CompanyEntity"]] = relationship(back_populates="user")


class CompanyEntity(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_companies_user_name"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    industry: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ats_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ats_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    user: Mapped["UserEntity"] = relationship(back_populates="companies")
    jobs: Mapped[list["JobEntity"]] = relationship(back_populates="company")
    job_intakes: Mapped[list["JobIntakeEntity"]] = relationship(back_populates="company_record")
    candidate_profiles: Mapped[list["CandidateProfileEntity"]] = relationship(back_populates="company_record")
    outreach_events: Mapped[list["OutreachEventEntity"]] = relationship(back_populates="company_record")
    inbound_email_replies: Mapped[list["InboundEmailReplyEntity"]] = relationship(back_populates="company_record")
    interview_sessions: Mapped[list["InterviewSessionEntity"]] = relationship(back_populates="company_record")
    orchestration_sessions: Mapped[list["OrchestrationSessionEntity"]] = relationship(back_populates="company_record")


class JobEntity(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="dashboard")
    job_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    vetting_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="volume")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skills_required: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    experience_level: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    compensation: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    structured_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    work_authorization: Mapped[str] = mapped_column(String(64), nullable=False, default="required")
    ats_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    auto_export_to_ats: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    remote_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    experience_required: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_candidate_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    company: Mapped["CompanyEntity"] = relationship(back_populates="jobs")
    interviews: Mapped[list["InterviewEntity"]] = relationship(back_populates="job")
    candidate_profiles: Mapped[list["CandidateProfileEntity"]] = relationship(back_populates="job")
    job_intakes: Mapped[list["JobIntakeEntity"]] = relationship(back_populates="job")
    scoring_profile: Mapped["ScoringProfileEntity | None"] = relationship(back_populates="job", uselist=False)
    feedback_items: Mapped[list["CandidateFeedbackEntity"]] = relationship(back_populates="job")
    ats_exports: Mapped[list["ATSExportEntity"]] = relationship(back_populates="job")
    outreach_events: Mapped[list["OutreachEventEntity"]] = relationship(back_populates="job")
    orchestration_sessions: Mapped[list["OrchestrationSessionEntity"]] = relationship(back_populates="job_record")


class JobIntakeEntity(Base):
    __tablename__ = "job_intakes"
    __table_args__ = (UniqueConstraint("job_id", name="uq_job_intakes_job"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_data_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    intake_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="job_intakes")
    company_record: Mapped["CompanyEntity"] = relationship(back_populates="job_intakes")


class InterviewEntity(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_interviews_job_candidate"),
        ForeignKeyConstraint(["job_id", "candidate_id"], ["candidate_profiles.job_id", "candidate_profiles.candidate_id"]),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="dashboard")
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="selected")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="interviews")


class CandidateProfileEntity(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_candidate_profiles_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    candidate_status: Mapped[str] = mapped_column(String(64), nullable=False, default="new")
    resume_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    total_experience_years: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    github_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    parsed_resume_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parsed_resume_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision: Mapped[str] = mapped_column(String(64), nullable=False, default="weak")
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="LOW")
    last_scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    ats_status: Mapped[str] = mapped_column(String(64), nullable=False, default="reviewed")
    ats_status_source: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    ats_status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ats_status_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    ats_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    job: Mapped["JobEntity"] = relationship(back_populates="candidate_profiles")
    company_record: Mapped["CompanyEntity"] = relationship(back_populates="candidate_profiles")
    company_record: Mapped["CompanyEntity"] = relationship(back_populates="candidate_profiles")


class CandidateLifecycleEventEntity(Base):
    __tablename__ = "candidate_lifecycle_events"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", "transition_key", name="uq_candidate_lifecycle_events_transition"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    transition_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    event_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class NotificationEventEntity(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        UniqueConstraint("notification_key", name="uq_notification_events_notification_key"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=True, index=True, default=None)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    actor_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    recipient_type: Mapped[str] = mapped_column(String(32), nullable=False, default="recruiter")
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="slack")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    notification_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    delivery_reference: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    notification_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AutomationJobEntity(Base):
    __tablename__ = "automation_jobs"
    __table_args__ = (
        UniqueConstraint("automation_key", name="uq_automation_jobs_automation_key"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=True, index=True, default=None)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    automation_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    automation_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    automation_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RecruiterNoteEntity(Base):
    __tablename__ = "recruiter_notes"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    recruiter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    note_type: Mapped[str] = mapped_column(String(32), nullable=False, default="note")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RecruiterTaskEntity(Base):
    __tablename__ = "recruiter_tasks"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    recruiter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class InterviewEvaluationEntity(Base):
    __tablename__ = "interview_evaluations"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", "stage_name", name="uq_interview_evaluations_job_candidate_stage"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    interviewer_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False, default="screen")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    competency_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class InternalCandidateResumeEntity(Base):
    __tablename__ = "internal_candidate_resumes"
    __table_args__ = (
        UniqueConstraint("resume_fingerprint", name="uq_internal_candidate_resumes_fingerprint"),
        UniqueConstraint("candidate_id", name="uq_internal_candidate_resumes_candidate_id"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resume_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    headline: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    years_experience: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    education: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    projects: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    domain_experience: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_resume_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    vector_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    qdrant_point_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

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
    recruiter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    session_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("candidate_selection_sessions.id"), nullable=True, index=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="feedback_items")


class RankingExplanationEntity(Base):
    __tablename__ = "ranking_explanations"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_ranking_explanations_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    existing_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recruiter_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    session_signal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recruiter_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RankingRunEntity(Base):
    __tablename__ = "ranking_runs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    recruiter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, default="initial")
    avg_existing_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_recruiter_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    percent_recruiter_capped: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drift_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RecruiterExperiencePreferenceEntity(Base):
    __tablename__ = "recruiter_experience_preferences"
    __table_args__ = (UniqueConstraint("recruiter_id", "experience_bucket", name="uq_recruiter_experience_preferences_recruiter_bucket"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    recruiter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    experience_bucket: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RecruiterSkillPreferenceEntity(Base):
    __tablename__ = "recruiter_skill_preferences"
    __table_args__ = (UniqueConstraint("recruiter_id", "skill", name="uq_recruiter_skill_preferences_recruiter_skill"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    recruiter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    skill: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RecruiterRolePreferenceEntity(Base):
    __tablename__ = "recruiter_role_preferences"
    __table_args__ = (UniqueConstraint("recruiter_id", "role", name="uq_recruiter_role_preferences_recruiter_role"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    recruiter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class CandidateSelectionSessionEntity(Base):
    __tablename__ = "candidate_selection_sessions"
    __table_args__ = ()

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_batch_index: Mapped[int] = mapped_column(nullable=False, default=0)
    batch_size: Mapped[int] = mapped_column(nullable=False, default=2)
    total_batches: Mapped[int] = mapped_column(nullable=False, default=3)
    candidate_pool_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    batch_plan: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False, default=list)
    selected_candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rejected_candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    batch_history: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    selection_analysis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_candidate_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class OrchestrationSessionEntity(Base):
    __tablename__ = "orchestration_sessions"
    __table_args__ = (UniqueConstraint("session_token", name="uq_orchestration_sessions_session_token"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    session_token: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="slack")
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="initiated")
    slack_team_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slack_channel_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slack_thread_ts: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    intake_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="slack")
    selected_path: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    current_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_question_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    current_question_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    current_question_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    structured_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_conversation: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    normalized_intake: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    voice_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    slack_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    voice_handoff_token: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    voice_handoff_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    voice_handoff_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    voice_token_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    state_version: Mapped[int] = mapped_column(nullable=False, default=0)
    last_processed_message_ts: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_processed_action_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_processed_transcript_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    intake_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    company_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=True, index=True, default=None)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=True, index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    company_record: Mapped["CompanyEntity | None"] = relationship(back_populates="orchestration_sessions")
    job_record: Mapped["JobEntity | None"] = relationship(back_populates="orchestration_sessions")
    events: Mapped[list["OrchestrationEventEntity"]] = relationship(back_populates="session_record")


class OrchestrationEventEntity(Base):
    __tablename__ = "orchestration_events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    session_id: Mapped[str] = mapped_column(GUID(), ForeignKey("orchestration_sessions.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    event_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="slack")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    session_record: Mapped["OrchestrationSessionEntity"] = relationship(back_populates="events")


class ATSExportEntity(Base):
    __tablename__ = "ats_exports"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", "provider", name="uq_ats_exports_job_candidate_provider"),
        ForeignKeyConstraint(["job_id", "candidate_id"], ["candidate_profiles.job_id", "candidate_profiles.candidate_id"]),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None, index=True)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="ats_exports")


class OutreachEventEntity(Base):
    __tablename__ = "outreach_events"
    __table_args__ = (
        ForeignKeyConstraint(["job_id", "candidate_id"], ["candidate_profiles.job_id", "candidate_profiles.candidate_id"]),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="dashboard")
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="sendgrid")
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    reply_state: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    archive_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    follow_up_count: Mapped[int] = mapped_column(nullable=False, default=0)
    open_count: Mapped[int] = mapped_column(nullable=False, default=0)
    reply_count: Mapped[int] = mapped_column(nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    message_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resume_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reply_intent: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    engagement_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reply_likelihood_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    responsiveness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    learning_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="outreach_events")
    company_record: Mapped["CompanyEntity"] = relationship(back_populates="outreach_events")


class InboundEmailReplyEntity(Base):
    __tablename__ = "inbound_email_replies"
    __table_args__ = (
        UniqueConstraint("svix_id", name="uq_inbound_email_replies_svix_id"),
        UniqueConstraint("email_id", name="uq_inbound_email_replies_email_id"),
        UniqueConstraint("provider_message_id", name="uq_inbound_email_replies_provider_message_id"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    svix_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="email.received")
    email_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=True, index=True, default=None)
    outreach_event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("outreach_events.id"), nullable=True, index=True, default=None)
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    webhook_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    match_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unmatched")
    intent: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processing_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    attachments: Mapped[list["InboundEmailAttachmentEntity"]] = relationship(back_populates="reply")
    outreach_event: Mapped["OutreachEventEntity | None"] = relationship()
    company_record: Mapped["CompanyEntity | None"] = relationship(back_populates="inbound_email_replies")


class InboundEmailAttachmentEntity(Base):
    __tablename__ = "inbound_email_attachments"
    __table_args__ = (
        UniqueConstraint("reply_id", "provider_attachment_id", name="uq_inbound_email_attachments_reply_provider_attachment"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    reply_id: Mapped[str] = mapped_column(GUID(), ForeignKey("inbound_email_replies.id"), nullable=False, index=True)
    provider_attachment_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    public_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    reply: Mapped["InboundEmailReplyEntity"] = relationship(back_populates="attachments")


class InterviewSessionEntity(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (UniqueConstraint("token", name="uq_interview_sessions_token"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(GUID(), ForeignKey("companies.id"), nullable=False, index=True)
    outreach_event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("outreach_events.id"), nullable=True, index=True, default=None)
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    token: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="requested")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    interviewer_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scheduling_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    evaluation_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    booking_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    company_record: Mapped["CompanyEntity"] = relationship(back_populates="interview_sessions")
    outreach_event: Mapped["OutreachEventEntity | None"] = relationship()


class NotificationWorkflowTokenEntity(Base):
    __tablename__ = "notification_workflow_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_notification_workflow_tokens_token"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="dashboard")
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    workflow_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    token: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class OtpEntity(Base):
    __tablename__ = "otps"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ATSExportRetryEntity(Base):
    """Retry queue for failed ATS exports."""
    __tablename__ = "ats_export_retries"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending | exhausted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class EmbeddingVersionRegistryEntity(Base):
    __tablename__ = "embedding_version_registry"
    __table_args__ = (UniqueConstraint("embedding_version", name="uq_embedding_version_registry_version"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AuditEventEntity(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
