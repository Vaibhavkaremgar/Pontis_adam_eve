from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, BigInteger
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym
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
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
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
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wallet_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True)

    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="users")
    slack_users: Mapped[list["SlackUserEntity"]] = relationship(back_populates="internal_user")
    slack_installations: Mapped[list["SlackInstallationEntity"]] = relationship(back_populates="installed_by_user")
    recruiter_interest_requests: Mapped[list["RecruiterInterestRequestEntity"]] = relationship(back_populates="recruiter")


class AllowedUserEntity(Base):
    __tablename__ = "allowed_users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    added_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CompanyEntity(Base):
    __tablename__ = "agencies"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    new_id: Mapped[str | None] = mapped_column(GUID(), nullable=True, default=None)
    # LinkedIn integration metadata only. Credentials and browser state remain
    # exclusively in Playwright's per-agency persistent profile directory.
    linkedin_email: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
    linkedin_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    linkedin_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    linkedin_last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    linkedin_profile_path: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    linkedin_connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    users: Mapped[list["UserEntity"]] = relationship(back_populates="agency")
    jobs: Mapped[list["JobEntity"]] = relationship(back_populates="agency")
    candidates: Mapped[list["CandidateProfileEntity"]] = relationship(back_populates="agency")
    job_intakes: Mapped[list["JobIntakeEntity"]] = relationship(back_populates="agency")
    outreach_events: Mapped[list["OutreachEventEntity"]] = relationship(back_populates="agency")
    inbound_email_replies: Mapped[list["InboundEmailReplyEntity"]] = relationship(back_populates="agency")
    interview_sessions: Mapped[list["InterviewSessionEntity"]] = relationship(back_populates="agency")
    orchestration_sessions: Mapped[list["OrchestrationSessionEntity"]] = relationship(back_populates="agency")
    slack_installations: Mapped[list["SlackInstallationEntity"]] = relationship(back_populates="agency")
    slack_users: Mapped[list["SlackUserEntity"]] = relationship(back_populates="agency")
    candidate_lifecycle_events: Mapped[list["CandidateLifecycleEventEntity"]] = relationship(back_populates="agency")
    notification_events: Mapped[list["NotificationEventEntity"]] = relationship(back_populates="agency")
    candidate_feedback: Mapped[list["CandidateFeedbackEntity"]] = relationship(back_populates="agency")
    candidate_requests: Mapped[list["CandidateRequestEntity"]] = relationship(back_populates="agency")
    recruiter_interest_requests: Mapped[list["RecruiterInterestRequestEntity"]] = relationship(back_populates="agency")
    ranking_explanations: Mapped[list["RankingExplanationEntity"]] = relationship(back_populates="agency")


class SlackInstallationEntity(Base):
    __tablename__ = "slack_installations"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_slack_installations_team_id"),
        Index("ix_slack_installations_agency_active", "agency_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    company_id: Mapped[str] = synonym("agency_id")
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    enterprise_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bot_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bot_access_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope_list: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    installed_by_user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency: Mapped["CompanyEntity"] = relationship(back_populates="slack_installations")
    installed_by_user: Mapped["UserEntity | None"] = relationship(back_populates="slack_installations")
    slack_users: Mapped[list["SlackUserEntity"]] = relationship(back_populates="installation")


class SlackUserEntity(Base):
    __tablename__ = "slack_users"
    __table_args__ = (
        UniqueConstraint("slack_installation_id", "slack_user_id", name="uq_slack_users_installation_user"),
        Index("ix_slack_users_agency_slack_user", "agency_id", "slack_user_id"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    company_id: Mapped[str] = synonym("agency_id")
    slack_installation_id: Mapped[str] = mapped_column(GUID(), ForeignKey("slack_installations.id"), nullable=False, index=True)
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    internal_user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="recruiter")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency: Mapped["CompanyEntity"] = relationship(back_populates="slack_users")
    installation: Mapped["SlackInstallationEntity"] = relationship(back_populates="slack_users")
    internal_user: Mapped["UserEntity | None"] = relationship(back_populates="slack_users")


class JobEntity(Base):
    __tablename__ = "job_descriptions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    vacancies: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skills: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_questions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    min_passing_score: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True)
    company_id: Mapped[str | None] = synonym("agency_id")
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(150), nullable=True)
    remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    company_website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    company_logo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valid_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shortlist_email_template_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shortlist_email_template_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_template_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True)
    created_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    updated_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="ui")
    job_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    vetting_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="volume")
    last_candidate_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    skills_required: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    experience_level: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    structured_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ats_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    remote_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)

    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="jobs")
    created_by_user: Mapped["UserEntity | None"] = relationship()
    candidate_profiles: Mapped[list["CandidateProfileEntity"]] = relationship(back_populates="job")
    job_intakes: Mapped[list["JobIntakeEntity"]] = relationship(back_populates="job")
    scoring_profile: Mapped["ScoringProfileEntity | None"] = relationship(back_populates="job", uselist=False)
    feedback_items: Mapped[list["CandidateFeedbackEntity"]] = relationship(back_populates="job")
    candidate_requests: Mapped[list["CandidateRequestEntity"]] = relationship(back_populates="job")
    recruiter_interest_requests: Mapped[list["RecruiterInterestRequestEntity"]] = relationship(back_populates="job")
    ats_exports: Mapped[list["ATSExportEntity"]] = relationship(back_populates="job")
    outreach_events: Mapped[list["OutreachEventEntity"]] = relationship(back_populates="job")
    orchestration_sessions: Mapped[list["OrchestrationSessionEntity"]] = relationship(back_populates="job_record")

    @property
    def compensation(self) -> str:
        return str(self.salary_range or "")

    @compensation.setter
    def compensation(self, value: str) -> None:
        self.salary_range = (value or "").strip()

    @property
    def work_authorization(self) -> str:
        structured = self.structured_data if isinstance(self.structured_data, dict) else {}
        return str(structured.get("work_authorization") or structured.get("workAuthorization") or "")

    @work_authorization.setter
    def work_authorization(self, value: str) -> None:
        structured = dict(self.structured_data or {})
        structured["work_authorization"] = (value or "").strip()
        structured["workAuthorization"] = (value or "").strip()
        self.structured_data = structured

    @property
    def auto_export_to_ats(self) -> bool:
        structured = self.structured_data if isinstance(self.structured_data, dict) else {}
        return bool(structured.get("auto_export_to_ats") or structured.get("autoExportToAts") or False)

    @auto_export_to_ats.setter
    def auto_export_to_ats(self, value: bool) -> None:
        structured = dict(self.structured_data or {})
        structured["auto_export_to_ats"] = bool(value)
        structured["autoExportToAts"] = bool(value)
        self.structured_data = structured


class JobIntakeEntity(Base):
    __tablename__ = "job_intakes"
    __table_args__ = (UniqueConstraint("job_id", name="uq_job_intakes_job"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_data_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    intake_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    company_id: Mapped[str] = synonym("agency_id")

    job: Mapped["JobEntity"] = relationship(back_populates="job_intakes")
    agency: Mapped["CompanyEntity"] = relationship(back_populates="job_intakes")


class InterviewEntity(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    interview_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    interviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    communication_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    culture_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_async: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    async_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    async_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    async_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    async_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    async_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    async_answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True)
    interview_score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical_score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    communication_score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    culture_fit_score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    updated_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="ui")

    candidate: Mapped["CandidateProfileEntity | None"] = relationship(
        primaryjoin="and_(foreign(InterviewEntity.candidate_id) == CandidateProfileEntity.candidate_id, "
                    "foreign(InterviewEntity.job_id) == CandidateProfileEntity.job_id)",
        viewonly=True,
    )
    agency: Mapped["CompanyEntity | None"] = relationship()


class CandidateProfileEntity(Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_candidate_profiles_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_company: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsing_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    skills: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    education: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    work_experience: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    offer_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_to_sheets: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    predefined_questions: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_communication_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_culture_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True)
    assigned_to_user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True)
    created_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    updated_by_source: Mapped[str] = mapped_column(String(30), nullable=False, default="PONTIS")
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parsed_resume_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    embedding_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ats_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ats_status_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ats_status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ats_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acquisition_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acquisition_status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_sending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_pending_acceptance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_retrying_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_queue_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acquisition_idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acquisition_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acquisition_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acquisition_account_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("linkedin_accounts.id"), nullable=True, index=True)
    acquisition_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workflow_token: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = synonym("agency_id")
    total_experience_years: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    job: Mapped["JobEntity | None"] = relationship(back_populates="candidate_profiles")
    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="candidates")

    @property
    def current_title(self) -> str:
        return str(self.current_role or "")

    @current_title.setter
    def current_title(self, value: str) -> None:
        self.current_role = (value or "").strip()

    @property
    def talent_pool_ready_at(self) -> datetime | None:
        return self.last_refreshed_at

    @talent_pool_ready_at.setter
    def talent_pool_ready_at(self, value: datetime | None) -> None:
        self.last_refreshed_at = value


class CandidateApplicationEntity(Base):
    __tablename__ = "candidate_applications"
    __table_args__ = (
        UniqueConstraint("application_fingerprint", name="uq_candidate_applications_fingerprint"),
        Index("ix_candidate_applications_job_status", "job_id", "application_status"),
        Index("ix_candidate_applications_job_email", "job_id", "email"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    agency_id: Mapped[str] = synonym("company_id")
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="", index=True)
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    resume_file_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    resume_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    resume_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resume_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    application_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    application_status: Mapped[str] = mapped_column(String(64), nullable=False, default="application_received")
    resume_processing_status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    resume_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evaluation_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    shortlist_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    shortlist_email_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    shortlisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class CandidateLifecycleEventEntity(Base):
    __tablename__ = "candidate_lifecycle_events"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", "transition_key", name="uq_candidate_lifecycle_events_transition"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    agency_id: Mapped[str] = synonym("company_id")
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    slack_installation_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("slack_installations.id"), nullable=True, index=True, default=None)
    slack_team_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    transition_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    event_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency: Mapped["CompanyEntity"] = relationship(back_populates="candidate_lifecycle_events")


class NotificationEventEntity(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        UniqueConstraint("notification_key", name="uq_notification_events_notification_key"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    agency_id: Mapped[str | None] = synonym("company_id")
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

    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="notification_events")


class AutomationJobEntity(Base):
    __tablename__ = "automation_jobs"
    __table_args__ = (
        UniqueConstraint("automation_key", name="uq_automation_jobs_automation_key"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True, default=None)
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
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    weight_pdl: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    weight_semantic: Mapped[float] = mapped_column(Float, nullable=False, default=0.45)
    weight_skill: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    weight_recency: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    feedback_bias: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    elite_reasoning_bonus: Mapped[float] = mapped_column(Float, nullable=False, default=0.08)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="scoring_profile")


class CandidateRequestEntity(Base):
    """Adam recruiter interest request; Eve may later transition pending consent."""
    __tablename__ = "candidate_requests"
    __table_args__ = (
        UniqueConstraint("agency_id", "job_id", "candidate_id", name="uq_candidate_requests_agency_job_candidate"),
        Index("ix_candidate_requests_candidate_status", "candidate_id", "status"),
        Index("ix_candidate_requests_agency_job_status", "agency_id", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    created_by: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agency: Mapped["CompanyEntity"] = relationship(back_populates="candidate_requests")
    job: Mapped["JobEntity"] = relationship(back_populates="candidate_requests")
    recruiter: Mapped["UserEntity"] = relationship()


class RecruiterInterestRequestEntity(Base):
    """Intent row created when a recruiter marks a candidate as interested."""

    __tablename__ = "recruiter_interest_requests"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "job_id",
            "agency_id",
            "recruiter_id",
            name="uq_recruiter_interest_requests_candidate_job_agency_recruiter",
        ),
        Index("ix_recruiter_interest_requests_candidate_job", "candidate_id", "job_id"),
        Index("ix_recruiter_interest_requests_agency_recruiter", "agency_id", "recruiter_id"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    agency_id: Mapped[str] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    recruiter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    request_status: Mapped[str] = mapped_column(String(32), nullable=False, default="interested", index=True)
    candidate_response: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    candidate_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    recruiter_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency: Mapped["CompanyEntity"] = relationship(back_populates="recruiter_interest_requests")
    job: Mapped["JobEntity"] = relationship(back_populates="recruiter_interest_requests")
    recruiter: Mapped["UserEntity"] = relationship(back_populates="recruiter_interest_requests")


class CandidateFeedbackEntity(Base):
    __tablename__ = "candidate_feedback"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_candidate_feedback_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    feedback: Mapped[str] = mapped_column(String(16), nullable=False)  # accept | reject
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    company_id: Mapped[str | None] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    agency_id: Mapped[str | None] = synonym("company_id")
    recruiter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    slack_installation_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("slack_installations.id"), nullable=True, index=True, default=None)
    slack_team_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    session_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("candidate_selection_sessions.id"), nullable=True, index=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    job: Mapped["JobEntity"] = relationship(back_populates="feedback_items")
    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="candidate_feedback")


class RankingExplanationEntity(Base):
    __tablename__ = "ranking_explanations"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_ranking_explanations_job_candidate"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    agency_id: Mapped[str] = synonym("company_id")
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    existing_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recruiter_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    session_signal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recruiter_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    agency: Mapped["CompanyEntity"] = relationship(back_populates="ranking_explanations")


class RankingRunEntity(Base):
    __tablename__ = "ranking_runs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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


class CandidateSelectionSessionEntity(Base):
    __tablename__ = "candidate_selection_sessions"
    __table_args__ = ()

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
    company_id: Mapped[str | None] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    agency_id: Mapped[str | None] = synonym("company_id")
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="orchestration_sessions")
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
        ForeignKeyConstraint(["job_id", "candidate_id"], ["candidates.job_id", "candidates.candidate_id"]),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
        ForeignKeyConstraint(["job_id", "candidate_id"], ["candidates.job_id", "candidates.candidate_id"]),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="ui")
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=False, index=True)
    agency_id: Mapped[str] = synonym("company_id")
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
    agency: Mapped["CompanyEntity"] = relationship(back_populates="outreach_events")


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
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    agency_id: Mapped[str | None] = synonym("company_id")
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
    agency: Mapped["CompanyEntity | None"] = relationship(back_populates="inbound_email_replies")


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
    __table_args__ = (UniqueConstraint("session_token", name="uq_interview_sessions_token"),)

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    job_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=True, index=True, default=None)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, default=None)
    company_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    outreach_event_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("outreach_events.id"), nullable=True, index=True, default=None)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    session_token: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True, default="scheduled")
    booking_status: Mapped[str | None] = mapped_column(String(255), nullable=True, default="pending")
    stage: Mapped[str | None] = mapped_column(String(255), nullable=True, default="requested")
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    available_slots: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    timezone: Mapped[str | None] = mapped_column(String(255), nullable=True, default="UTC")
    interviewer_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scheduling_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluation_status: Mapped[str | None] = mapped_column(String(255), nullable=True, default="pending")
    evaluation_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    booking_url: Mapped[str | None] = mapped_column(String(1024), nullable=True, default=None)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=True)
    created_by_source: Mapped[str] = mapped_column(String(255), nullable=False, default="PONTIS")
    updated_by_source: Mapped[str] = mapped_column(String(255), nullable=False, default="PONTIS")

    agency: Mapped["CompanyEntity"] = relationship(back_populates="interview_sessions")
    outreach_event: Mapped["OutreachEventEntity | None"] = relationship()

    @property
    def token(self) -> str:
        return self.session_token or ""

    @token.setter
    def token(self, value: str) -> None:
        self.session_token = value


class NotificationWorkflowTokenEntity(Base):
    __tablename__ = "notification_workflow_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_notification_workflow_tokens_token"),
    )

    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    source_app: Mapped[str] = mapped_column(String(32), nullable=False, default="ui")
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    agency_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    token_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    workflow_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    token: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
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
    job_id: Mapped[str] = mapped_column(GUID(), ForeignKey("job_descriptions.id"), nullable=False, index=True)
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
    company_id: Mapped[str | None] = mapped_column("agency_id", GUID(), ForeignKey("agencies.id"), nullable=True, index=True, default=None)
    user_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True, index=True, default=None)
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

