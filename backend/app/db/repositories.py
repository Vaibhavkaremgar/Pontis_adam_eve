from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.utils.exceptions import APIError
from app.models.entities import (
    ATSExportEntity,
    ATSExportRetryEntity,
    CandidateFeedbackEntity,
    AutomationJobEntity,
    CandidateLifecycleEventEntity,
    CandidateProfileEntity,
    CompanyEntity,
    JobIntakeEntity,
    InboundEmailAttachmentEntity,
    InboundEmailReplyEntity,
    InternalCandidateResumeEntity,
    CandidateSelectionSessionEntity,
    InterviewEntity,
    InterviewSessionEntity,
    JobEntity,
    OtpEntity,
    OrchestrationEventEntity,
    OrchestrationSessionEntity,
    OutreachEventEntity,
    NotificationWorkflowTokenEntity,
    NotificationEventEntity,
    RecruiterNoteEntity,
    RecruiterTaskEntity,
    InterviewEvaluationEntity,
    RankingExplanationEntity,
    RankingRunEntity,
    RecruiterExperiencePreferenceEntity,
    RecruiterRolePreferenceEntity,
    RecruiterSkillPreferenceEntity,
    ScoringProfileEntity,
    UserEntity,
)
from app.core.config import ENABLE_FAKE_EMAILS, RLHF_BASE_FEEDBACK_BIAS, RLHF_MIN_FEEDBACK_BIAS, RLHF_SMOOTHING_ALPHA

logger = logging.getLogger(__name__)
ADAM_SOURCE_APP = "adam"
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}", re.IGNORECASE)


def ensure_candidate_profile(db: Session, job_id: str, candidate_id: str) -> CandidateProfileEntity:
    return CandidateProfileRepository(db).ensure_candidate_profile(job_id=job_id, candidate_id=candidate_id)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _candidate_email_value(value: object) -> str:
    if isinstance(value, dict):
        for item in value.values():
            extracted = _candidate_email_value(item)
            if extracted:
                return extracted
        return ""

    if isinstance(value, list):
        for item in value:
            extracted = _candidate_email_value(item)
            if extracted:
                return extracted
        return ""

    email = _normalize_text(value).lower()
    if "@" in email:
        match = _EMAIL_PATTERN.search(email)
        return match.group(0).lower() if match else email
    match = _EMAIL_PATTERN.search(email)
    return match.group(0).lower() if match else ""


def _candidate_email_values(value: object) -> list[str]:
    values: list[str] = []

    def _collect(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                _collect(nested)
            return
        if isinstance(item, list):
            for nested in item:
                _collect(nested)
            return
        email = _candidate_email_value(item)
        if email and not email.endswith("@test.local"):
            values.append(email)

    _collect(value)
    seen: set[str] = set()
    unique: list[str] = []
    for email in values:
        if email in seen:
            continue
        seen.add(email)
        unique.append(email)
    return unique


def _build_dev_email(*, name: str, candidate_id: str) -> str:
    safe_name = re.sub(r"[^a-z0-9]+", "", _normalize_text(name).lower()) or "candidate"
    safe_id = re.sub(r"[^a-z0-9]+", "", _normalize_text(candidate_id).lower())[:6] or "000000"
    return f"{safe_name}_{safe_id}@test.local"


def _job_company_id(db: Session, job_id: str) -> str | None:
    job = db.scalar(select(JobEntity).where(JobEntity.id == job_id, JobEntity.source_app == ADAM_SOURCE_APP))
    if not job:
        return None
    return str(job.company_id or "").strip() or None


def _ensure_candidate_profile_email(row: CandidateProfileEntity) -> bool:
    raw_data = dict(row.raw_data) if isinstance(row.raw_data, dict) else {}
    existing_candidates = _candidate_email_values(raw_data)
    existing = existing_candidates[0] if existing_candidates else ""
    if existing:
        raw_data.update(
            {
                "work_email": raw_data.get("work_email") or existing,
                "email": raw_data.get("email") or existing,
                "personal_email": raw_data.get("personal_email") or existing,
                "emails_primary": raw_data.get("emails_primary") or existing,
            }
        )
        row.raw_data = raw_data
        return True

    if not ENABLE_FAKE_EMAILS:
        return False

    generated = _build_dev_email(name=row.name or row.candidate_id, candidate_id=row.candidate_id)
    raw_data.update(
        {
            "work_email": generated,
            "email": generated,
            "personal_email": generated,
            "is_mock_email": True,
            "email_source": "generated",
        }
    )
    row.raw_data = raw_data
    return True


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str) -> UserEntity | None:
        return self.db.scalar(select(UserEntity).where(UserEntity.email == email))

    def create(self, email: str, *, role: str = "recruiter") -> UserEntity:
        entity = UserEntity(id=str(uuid4()), email=email.lower().strip(), role=(role or "recruiter").strip().lower() or "recruiter")
        self.db.add(entity)
        self.db.flush()
        return entity


class CompanyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _normalize_name(name: str) -> str:
        return (name or "").strip().lower()

    def get_by_user_and_name(self, *, user_id: str, name: str) -> CompanyEntity | None:
        normalized_name = self._normalize_name(name)
        if not normalized_name:
            return None
        return self.db.scalar(
            select(CompanyEntity).where(
                CompanyEntity.user_id == user_id,
                CompanyEntity.name == normalized_name,
            )
        )

    def get_by_id(self, company_id: str) -> CompanyEntity | None:
        return self.db.scalar(select(CompanyEntity).where(CompanyEntity.id == company_id))

    def get_latest_for_user(self, *, user_id: str) -> CompanyEntity | None:
        return self.db.scalar(
            select(CompanyEntity)
            .where(CompanyEntity.user_id == user_id)
            .order_by(CompanyEntity.created_at.desc())
        )

    def create(
        self,
        *,
        user_id: str,
        name: str,
        website: str,
        description: str,
        industry: str = "",
    ) -> CompanyEntity:
        entity = CompanyEntity(
            id=str(uuid4()),
            user_id=user_id,
            name=self._normalize_name(name),
            website=website.strip(),
            description=description.strip(),
            industry=industry.strip(),
            ats_provider="",
            ats_connected=False,
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def get_or_create(
        self,
        *,
        user_id: str,
        name: str,
        website: str,
        description: str,
        industry: str = "",
    ) -> CompanyEntity:
        normalized_name = self._normalize_name(name)
        existing = self.get_by_user_and_name(user_id=user_id, name=normalized_name)
        if existing:
            logger.info("company_reused user_id=%s company_id=%s name=%s", user_id, existing.id, normalized_name)
            return existing

        row = CompanyEntity(
            id=str(uuid4()),
            user_id=user_id,
            name=normalized_name,
            website=website.strip(),
            description=description.strip(),
            industry=industry.strip(),
            ats_provider="",
            ats_connected=False,
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            logger.info("company_created user_id=%s company_id=%s name=%s", user_id, row.id, normalized_name)
            return row
        except IntegrityError:
            existing = self.get_by_user_and_name(user_id=user_id, name=normalized_name)
            if existing:
                logger.info(
                    "company_reused_after_conflict user_id=%s company_id=%s name=%s",
                    user_id,
                    existing.id,
                    normalized_name,
                )
                return existing
            raise

    def update_profile(
        self,
        *,
        company_id: str,
        name: str | None = None,
        description: str | None = None,
        industry: str | None = None,
        ats_provider: str | None = None,
        ats_connected: bool | None = None,
    ) -> CompanyEntity | None:
        company = self.db.scalar(select(CompanyEntity).where(CompanyEntity.id == company_id))
        if not company:
            return None

        if name is not None:
            company.name = self._normalize_name(name)
        if description is not None:
            company.description = description.strip()
        if industry is not None:
            company.industry = industry.strip()
        if ats_provider is not None:
            company.ats_provider = ats_provider.strip().lower()
        if ats_connected is not None:
            company.ats_connected = bool(ats_connected)

        self.db.flush()
        return company

    def upsert_for_user(
        self,
        *,
        user_id: str,
        name: str,
        website: str,
        description: str,
        industry: str = "",
    ) -> CompanyEntity:
        normalized_name = self._normalize_name(name)
        existing = self.get_by_user_and_name(user_id=user_id, name=normalized_name)
        if existing:
            existing.website = website.strip()
            existing.description = description.strip()
            existing.industry = industry.strip()
            self.db.flush()
            return existing
        return self.create(
            user_id=user_id,
            name=name,
            website=website,
            description=description,
            industry=industry,
        )


class JobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        company_id: str,
        created_by: str,
        title: str,
        description: str,
        location: str,
        compensation: str,
        work_authorization: str,
        vetting_mode: str = "volume",
        auto_export_to_ats: bool = False,
        ats_job_id: str | None = None,
        remote_policy: str = "",
        experience_required: str = "",
        responsibilities: list[str] | None = None,
        skills_required: list[str] | None = None,
        experience_level: str = "",
        structured_data: dict | None = None,
    ) -> JobEntity:
        entity = JobEntity(
            id=str(uuid4()),
            source_app=ADAM_SOURCE_APP,
            company_id=company_id,
            title=title.strip(),
            description=description.strip(),
            responsibilities=list(responsibilities or []),
            skills_required=list(skills_required or []),
            experience_level=experience_level.strip(),
            location=location.strip(),
            compensation=compensation.strip(),
            structured_data=dict(structured_data or {}),
            work_authorization=work_authorization.strip(),
            ats_job_id=(ats_job_id or "").strip() or None,
            vetting_mode=((vetting_mode or "volume").strip().lower() if (vetting_mode or "").strip().lower() in {"volume", "elite"} else "volume"),
            auto_export_to_ats=bool(auto_export_to_ats),
            created_by=created_by,
            remote_policy=remote_policy.strip(),
            experience_required=experience_required.strip(),
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def get(self, job_id: str) -> JobEntity | None:
        return self.db.scalar(
            select(JobEntity).where(
                JobEntity.id == job_id,
                JobEntity.source_app == ADAM_SOURCE_APP,
            )
        )

    def get_recruiter_id(self, job_id: str) -> str | None:
        job = self.get(job_id)
        if not job:
            return None
        company = CompanyRepository(self.db).get_by_id(job.company_id)
        if not company:
            return None
        return str(company.user_id or "").strip() or None

    def update_candidate_sourcing_state(
        self,
        *,
        job_id: str,
        job_status: str,
        last_candidate_attempt_at: datetime | None = None,
    ) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None
        job.job_status = job_status.strip().lower()
        if last_candidate_attempt_at is not None:
            job.last_candidate_attempt_at = last_candidate_attempt_at
        job.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return job

    def get_candidate_sourcing_state(self, job_id: str) -> tuple[str, datetime | None] | None:
        job = self.get(job_id)
        if not job:
            return None
        return (job.job_status or "active").strip().lower(), job.last_candidate_attempt_at

    def list_recent(self, limit: int = 50) -> list[JobEntity]:
        rows = self.db.scalars(
            select(JobEntity)
            .where(JobEntity.source_app == ADAM_SOURCE_APP)
            .order_by(JobEntity.created_at.desc())
            .limit(limit)
        ).all()
        return list(rows)

    def update_description(self, job_id: str, description: str) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None
        job.description = description
        job.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return job

    def update_structured_fields(
        self,
        *,
        job_id: str,
        title: str | None = None,
        description: str | None = None,
        responsibilities: list[str] | None = None,
        skills_required: list[str] | None = None,
        experience_level: str | None = None,
        location: str | None = None,
        compensation: str | None = None,
        remote_policy: str | None = None,
        experience_required: str | None = None,
        vetting_mode: str | None = None,
        auto_export_to_ats: bool | None = None,
        ats_job_id: str | None = None,
        structured_data: dict | None = None,
    ) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None

        if title is not None:
            job.title = title.strip()
        if description is not None:
            job.description = description.strip()
        if responsibilities is not None:
            job.responsibilities = responsibilities
        if skills_required is not None:
            job.skills_required = skills_required
        if experience_level is not None:
            job.experience_level = experience_level.strip()
        if location is not None:
            job.location = location.strip()
        if compensation is not None:
            job.compensation = compensation.strip()
        if vetting_mode is not None:
            normalized = (vetting_mode or "volume").strip().lower()
            job.vetting_mode = normalized if normalized in {"volume", "elite"} else "volume"
        if auto_export_to_ats is not None:
            job.auto_export_to_ats = bool(auto_export_to_ats)
        if ats_job_id is not None:
            job.ats_job_id = (ats_job_id or "").strip() or None
        if remote_policy is not None:
            job.remote_policy = remote_policy.strip()
        if experience_required is not None:
            job.experience_required = experience_required.strip()
        if structured_data is not None:
            job.structured_data = structured_data

        job.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return job

    def set_vetting_mode(self, *, job_id: str, vetting_mode: str) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None
        normalized = (vetting_mode or "volume").strip().lower()
        job.vetting_mode = normalized if normalized in {"volume", "elite"} else "volume"
        job.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return job


class JobIntakeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_job(self, job_id: str) -> JobIntakeEntity | None:
        normalized = (job_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(JobIntakeEntity).where(JobIntakeEntity.job_id == normalized))

    def upsert_completed_intake(
        self,
        *,
        job_id: str,
        transcript: str,
        structured_data_json: dict,
        intake_status: str = "completed",
        completed_at: datetime | None = None,
    ) -> JobIntakeEntity:
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)

        row = self.get_by_job(job_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = JobIntakeEntity(
                id=str(uuid4()),
                job_id=job.id,
                company_id=job.company_id,
                transcript="",
                structured_data_json={},
                intake_status="pending",
            )
            self.db.add(row)
            self.db.flush()

        row.company_id = job.company_id
        row.transcript = transcript.strip()
        row.structured_data_json = dict(structured_data_json or {})
        row.intake_status = (intake_status or "completed").strip().lower() or "completed"
        row.completed_at = completed_at or now
        row.updated_at = now
        self.db.flush()
        return row


class OrchestrationSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, session_id: str) -> OrchestrationSessionEntity | None:
        normalized = (session_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(OrchestrationSessionEntity).where(OrchestrationSessionEntity.id == normalized))

    def get_by_token(self, session_token: str) -> OrchestrationSessionEntity | None:
        normalized = (session_token or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(OrchestrationSessionEntity).where(OrchestrationSessionEntity.session_token == normalized))

    def get_by_voice_handoff_token(self, voice_handoff_token: str) -> OrchestrationSessionEntity | None:
        normalized = (voice_handoff_token or "").strip()
        if not normalized:
            return None
        return self.db.scalar(
            select(OrchestrationSessionEntity).where(OrchestrationSessionEntity.voice_handoff_token == normalized)
        )

    def get_by_job(self, job_id: str) -> OrchestrationSessionEntity | None:
        normalized = (job_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(
            select(OrchestrationSessionEntity)
            .where(OrchestrationSessionEntity.job_id == normalized)
            .order_by(OrchestrationSessionEntity.updated_at.desc(), OrchestrationSessionEntity.created_at.desc())
        )

    def get_active_by_slack_context(
        self,
        *,
        slack_team_id: str = "",
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
        slack_user_id: str = "",
        source: str = "slack",
    ) -> OrchestrationSessionEntity | None:
        normalized_source = (source or "slack").strip().lower() or "slack"
        conditions = [OrchestrationSessionEntity.source == normalized_source]
        if slack_team_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_team_id == slack_team_id.strip())
        if slack_channel_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_channel_id == slack_channel_id.strip())
        if slack_thread_ts.strip():
            conditions.append(OrchestrationSessionEntity.slack_thread_ts == slack_thread_ts.strip())
        if slack_user_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_user_id == slack_user_id.strip())
        conditions.extend(
            [
                or_(OrchestrationSessionEntity.expires_at.is_(None), OrchestrationSessionEntity.expires_at > datetime.now(timezone.utc)),
                OrchestrationSessionEntity.completed_at.is_(None),
            ]
        )
        stmt = select(OrchestrationSessionEntity).where(*conditions).order_by(OrchestrationSessionEntity.updated_at.desc())
        return self.db.scalar(stmt)

    def archive_active_by_slack_context(
        self,
        *,
        slack_team_id: str = "",
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
        slack_user_id: str = "",
        source: str = "slack",
        exclude_session_id: str = "",
    ) -> list[OrchestrationSessionEntity]:
        normalized_source = (source or "slack").strip().lower() or "slack"
        conditions = [OrchestrationSessionEntity.source == normalized_source]
        if slack_team_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_team_id == slack_team_id.strip())
        if slack_channel_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_channel_id == slack_channel_id.strip())
        if slack_thread_ts.strip():
            conditions.append(OrchestrationSessionEntity.slack_thread_ts == slack_thread_ts.strip())
        if slack_user_id.strip():
            conditions.append(OrchestrationSessionEntity.slack_user_id == slack_user_id.strip())
        conditions.extend(
            [
                or_(OrchestrationSessionEntity.expires_at.is_(None), OrchestrationSessionEntity.expires_at > datetime.now(timezone.utc)),
                OrchestrationSessionEntity.completed_at.is_(None),
            ]
        )
        stmt = select(OrchestrationSessionEntity).where(*conditions).order_by(OrchestrationSessionEntity.updated_at.desc())
        rows = list(self.db.scalars(stmt).all())
        now = datetime.now(timezone.utc)
        archived: list[OrchestrationSessionEntity] = []
        for row in rows:
            if exclude_session_id and str(getattr(row, "id", "")).strip() == exclude_session_id.strip():
                continue
            row.current_stage = "closed"
            row.completed_at = row.completed_at or now
            row.updated_at = now
            if hasattr(row, "state_version"):
                try:
                    row.state_version = int(getattr(row, "state_version", 0) or 0) + 1
                except (TypeError, ValueError):
                    row.state_version = 1
            archived.append(row)
        if archived:
            self.db.flush()
        return archived

    def create(
        self,
        *,
        session_token: str,
        source: str = "slack",
        current_stage: str = "initiated",
        slack_team_id: str = "",
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
        slack_user_id: str = "",
        intake_mode: str = "slack",
        selected_path: str = "",
        current_question: str = "",
        current_question_key: str = "",
        structured_context: dict | None = None,
        raw_conversation: list | None = None,
        normalized_intake: dict | None = None,
        voice_context: dict | None = None,
        slack_context: dict | None = None,
        expires_at: datetime | None = None,
        company_id: str | None = None,
        job_id: str | None = None,
    ) -> OrchestrationSessionEntity:
        row = OrchestrationSessionEntity(
            id=str(uuid4()),
            session_token=(session_token or "").strip(),
            source=(source or "slack").strip().lower() or "slack",
            current_stage=(current_stage or "initiated").strip().lower() or "initiated",
            slack_team_id=(slack_team_id or "").strip(),
            slack_channel_id=(slack_channel_id or "").strip(),
            slack_thread_ts=(slack_thread_ts or "").strip(),
            slack_user_id=(slack_user_id or "").strip(),
            intake_mode=(intake_mode or "slack").strip().lower() or "slack",
            selected_path=(selected_path or "").strip().lower(),
            current_question=(current_question or "").strip(),
            current_question_key=(current_question_key or "").strip(),
            current_question_type="",
            current_question_schema={},
            structured_context=dict(structured_context or {}),
            raw_conversation=list(raw_conversation or []),
            normalized_intake=dict(normalized_intake or {}),
            voice_context=dict(voice_context or {}),
            slack_context=dict(slack_context or {}),
            state_version=0,
            last_processed_message_ts="",
            last_processed_action_hash="",
            last_processed_transcript_hash="",
            intake_version="v1",
            expires_at=expires_at,
            company_id=(company_id or "").strip() or None,
            job_id=(job_id or "").strip() or None,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def update(self, row: OrchestrationSessionEntity, **fields: object) -> OrchestrationSessionEntity:
        for key, value in fields.items():
            if not hasattr(row, key):
                continue
            setattr(row, key, value)
        if hasattr(row, "state_version"):
            try:
                row.state_version = int(getattr(row, "state_version", 0) or 0) + 1
            except (TypeError, ValueError):
                row.state_version = 1
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return row

    def list_recent(self, *, limit: int = 100) -> list[OrchestrationSessionEntity]:
        rows = self.db.scalars(
            select(OrchestrationSessionEntity)
            .order_by(OrchestrationSessionEntity.updated_at.desc(), OrchestrationSessionEntity.created_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)


class OrchestrationEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        session_id: str,
        event_type: str,
        event_payload: dict | None = None,
        source: str = "slack",
    ) -> OrchestrationEventEntity:
        row = OrchestrationEventEntity(
            id=str(uuid4()),
            session_id=session_id,
            event_type=(event_type or "").strip().upper(),
            event_payload=dict(event_payload or {}),
            source=(source or "slack").strip().lower() or "slack",
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_for_session(self, session_id: str, *, limit: int = 500) -> list[OrchestrationEventEntity]:
        rows = self.db.scalars(
            select(OrchestrationEventEntity)
            .where(OrchestrationEventEntity.session_id == session_id)
            .order_by(OrchestrationEventEntity.created_at.asc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)


class InterviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_job_and_candidate(self, job_id: str, candidate_id: str) -> InterviewEntity | None:
        return self.db.scalar(
            select(InterviewEntity).where(
                InterviewEntity.job_id == job_id,
                InterviewEntity.candidate_id == candidate_id,
                InterviewEntity.source_app == ADAM_SOURCE_APP,
            )
        )

    def upsert_status(self, *, job_id: str, candidate_id: str, status: str, create_default: str = "shortlisted") -> InterviewEntity:
        candidate_id = (candidate_id or "").strip()
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
        if not row:
            row = InterviewEntity(
                id=str(uuid4()),
                source_app=ADAM_SOURCE_APP,
                job_id=job_id,
                company_id=job.company_id,
                candidate_id=candidate_id,
                status=create_default,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                logger.info("interview_duplicate_skipped job_id=%s candidate_id=%s", job_id, candidate_id)
                row = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
                if not row:
                    raise

        row.status = status
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[InterviewEntity]:
        rows = self.db.scalars(
            select(InterviewEntity)
            .where(
                InterviewEntity.job_id == job_id,
                InterviewEntity.source_app == ADAM_SOURCE_APP,
            )
        ).all()
        return list(rows)


class InterviewSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_job_and_candidate(self, *, job_id: str, candidate_id: str) -> InterviewSessionEntity | None:
        normalized_job_id = (job_id or "").strip()
        normalized_candidate_id = (candidate_id or "").strip()
        if not normalized_job_id or not normalized_candidate_id:
            return None
        return self.db.scalar(
            select(InterviewSessionEntity).where(
                InterviewSessionEntity.job_id == normalized_job_id,
                InterviewSessionEntity.candidate_id == normalized_candidate_id,
            ).order_by(InterviewSessionEntity.expires_at.desc())
        )

    def create(
        self,
        *,
        job_id: str,
        candidate_id: str,
        email: str,
        token: str,
        expires_at: datetime,
        booking_url: str = "",
        outreach_event_id: str | None = None,
        status: str = "pending",
    ) -> InterviewSessionEntity:
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        existing_session = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
        if existing_session and (existing_session.expires_at is None or existing_session.expires_at > datetime.now(timezone.utc)):
            existing_session.email = email
            existing_session.status = status if (existing_session.status or "").strip().lower() != "booked" else existing_session.status
            existing_session.token = existing_session.token or token
            existing_session.expires_at = expires_at if not existing_session.expires_at or existing_session.expires_at < expires_at else existing_session.expires_at
            existing_session.company_id = job.company_id
            if outreach_event_id is not None:
                existing_session.outreach_event_id = outreach_event_id
            if booking_url:
                existing_session.booking_url = booking_url
            self.db.flush()
            return existing_session

        existing = self.get_by_token(token)
        if existing:
            existing.job_id = job_id
            existing.candidate_id = candidate_id
            existing.email = email
            existing.expires_at = expires_at
            existing.status = status
            existing.booked_at = None
            existing.company_id = job.company_id
            existing.outreach_event_id = outreach_event_id
            existing.booking_url = booking_url or existing.booking_url
            self.db.flush()
            return existing

        row = InterviewSessionEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=candidate_id,
            company_id=job.company_id,
            outreach_event_id=outreach_event_id,
            email=email,
            token=token,
            status=status,
            expires_at=expires_at,
            booking_url=booking_url,
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            return row
        except IntegrityError:
            existing = self.get_by_token(token)
            if existing:
                return existing
            existing_session = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
            if existing_session:
                return existing_session
            raise

    def get_by_token(self, token: str) -> InterviewSessionEntity | None:
        normalized = (token or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(InterviewSessionEntity).where(InterviewSessionEntity.token == normalized))

    def mark_booked(self, token: str) -> InterviewSessionEntity | None:
        row = self.get_by_token(token)
        if not row:
            return None
        row.status = "booked"
        row.booked_at = datetime.now(timezone.utc)
        self.db.flush()
        return row


class CandidateProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, *, job_id: str, candidate_id: str) -> CandidateProfileEntity | None:
        row = self.db.scalar(
            select(CandidateProfileEntity).where(
                CandidateProfileEntity.job_id == job_id,
                CandidateProfileEntity.candidate_id == candidate_id,
            )
        )
        if row and not getattr(row, "company_id", "").strip():
            job = JobRepository(self.db).get(job_id)
            if job:
                row.company_id = job.company_id
        if row and not _normalize_text(getattr(row, "ats_status", "")):
            row.ats_status = "review_pending"
            row.ats_status_source = "system"
            row.ats_status_reason = ""
            row.ats_status_updated_at = datetime.now(timezone.utc)
        if row and _ensure_candidate_profile_email(row):
            self.db.flush()
        return row

    @staticmethod
    def _profile_email_values(row: CandidateProfileEntity) -> list[str]:
        raw_data = row.raw_data if isinstance(row.raw_data, dict) else {}
        return _candidate_email_values(raw_data)

    def find_by_email(self, email: str) -> CandidateProfileEntity | None:
        normalized = _candidate_email_value(email)
        if not normalized:
            return None

        rows = self.db.scalars(
            select(CandidateProfileEntity).order_by(CandidateProfileEntity.last_scored_at.desc())
        ).all()
        for row in rows:
            if _ensure_candidate_profile_email(row):
                self.db.flush()
            if normalized in self._profile_email_values(row):
                return row
        return None

    @staticmethod
    def _is_fallback_candidate_id(candidate_id: str) -> bool:
        normalized = (candidate_id or "").strip().lower()
        return normalized.startswith("fallback-candidate")

    def ensure_candidate_profile(self, *, job_id: str, candidate_id: str) -> CandidateProfileEntity:
        normalized_candidate_id = (candidate_id or "").strip()
        if not normalized_candidate_id:
            raise APIError("candidate_id is required", status_code=400)

        if self._is_fallback_candidate_id(normalized_candidate_id):
            logger.warning(
                "fallback_candidate_blocked job_id=%s candidate_id=%s",
                job_id,
                normalized_candidate_id,
            )
            raise APIError("fallback candidate ids are not allowed", status_code=400)

        existing = self.get(job_id=job_id, candidate_id=normalized_candidate_id)
        if existing:
            return existing

        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)

        row = CandidateProfileEntity(
            id=str(uuid4()),
            job_id=job_id,
            company_id=job.company_id,
            candidate_id=normalized_candidate_id,
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            logger.info(
                "candidate_profile_created_missing job_id=%s candidate_id=%s",
                job_id,
                normalized_candidate_id,
            )
            return row
        except IntegrityError:
            existing = self.get(job_id=job_id, candidate_id=normalized_candidate_id)
            if existing:
                return existing
            raise

    def upsert(
        self,
        *,
        job_id: str,
        candidate_id: str,
        name: str,
        role: str,
        company: str,
        summary: str,
        skills: list[str],
        raw_data: dict,
        fit_score: float,
        decision: str,
        strategy: str,
    ) -> CandidateProfileEntity:
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        if not row:
            row = CandidateProfileEntity(
                id=str(uuid4()),
                job_id=job_id,
                company_id=job.company_id,
                candidate_id=candidate_id,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                logger.info("candidate_profile_duplicate_skipped job_id=%s candidate_id=%s", job_id, candidate_id)
                row = self.get(job_id=job_id, candidate_id=candidate_id)
                if not row:
                    raise

        row.name = name.strip()
        row.role = role.strip()
        row.company = company.strip()
        row.summary = summary.strip()
        row.skills = skills
        row.raw_data = raw_data
        row.company_id = job.company_id
        row.fit_score = fit_score
        row.decision = decision
        row.strategy = strategy
        row.last_scored_at = now
        row.last_refreshed_at = now
        if not _normalize_text(row.ats_status):
            row.ats_status = "sourced"
        if not _normalize_text(row.ats_status_source):
            row.ats_status_source = "ingestion"
        row.ats_status_updated_at = now
        if _ensure_candidate_profile_email(row):
            logger.info("candidate_profile_dev_email_backfilled job_id=%s candidate_id=%s", job_id, candidate_id)
        self.db.flush()
        return row

    def touch_refresh(self, *, job_id: str, candidate_id: str) -> CandidateProfileEntity | None:
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        if not row:
            return None
        now = datetime.now(timezone.utc)
        row.last_refreshed_at = now
        self.db.flush()
        return row

    def list_stale(self, *, limit: int, stale_before: datetime) -> list[CandidateProfileEntity]:
        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .where(CandidateProfileEntity.last_refreshed_at < stale_before)
            .order_by(CandidateProfileEntity.last_refreshed_at.asc())
            .limit(limit)
        ).all()
        return list(rows)

    def list_for_migration(self, *, limit: int) -> list[CandidateProfileEntity]:
        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .order_by(CandidateProfileEntity.last_refreshed_at.asc(), CandidateProfileEntity.last_scored_at.asc())
            .limit(limit)
        ).all()
        return list(rows)

    def list_for_job(self, job_id: str) -> list[CandidateProfileEntity]:
        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .where(CandidateProfileEntity.job_id == job_id)
            .order_by(CandidateProfileEntity.fit_score.desc())
        ).all()
        updated = False
        for row in rows:
            updated = _ensure_candidate_profile_email(row) or updated
        if updated:
            self.db.flush()
        return list(rows)

    def count_for_job(self, job_id: str) -> int:
        count = self.db.scalar(
            select(func.count())
            .select_from(CandidateProfileEntity)
            .where(CandidateProfileEntity.job_id == job_id)
        )
        return int(count or 0)

    def latest_by_candidate_ids(self, *, job_id: str, candidate_ids: list[str]) -> dict[str, CandidateProfileEntity]:
        unique_ids = [str(candidate_id) for candidate_id in candidate_ids if str(candidate_id).strip()]
        if not unique_ids:
            return {}

        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .where(
                CandidateProfileEntity.candidate_id.in_(unique_ids),
                CandidateProfileEntity.job_id == job_id,
            )
            .order_by(CandidateProfileEntity.last_scored_at.desc())
        ).all()

        latest: dict[str, CandidateProfileEntity] = {}
        for row in rows:
            if _ensure_candidate_profile_email(row):
                self.db.flush()
            if row.candidate_id not in latest:
                latest[row.candidate_id] = row
        return latest


class InternalCandidateResumeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_fingerprint(self, fingerprint: str) -> InternalCandidateResumeEntity | None:
        normalized = _normalize_text(fingerprint)
        if not normalized:
            return None
        return self.db.scalar(
            select(InternalCandidateResumeEntity).where(
                InternalCandidateResumeEntity.resume_fingerprint == normalized,
            )
        )

    def get_by_candidate_id(self, candidate_id: str) -> InternalCandidateResumeEntity | None:
        normalized = _normalize_text(candidate_id)
        if not normalized:
            return None
        return self.db.scalar(
            select(InternalCandidateResumeEntity).where(
                InternalCandidateResumeEntity.candidate_id == normalized,
            )
        )

    def upsert(
        self,
        **payload,
    ) -> InternalCandidateResumeEntity:
        now = datetime.now(timezone.utc)
        normalized_payload = dict(payload or {})
        candidate_id = _normalize_text(
            normalized_payload.get("candidate_id")
            or normalized_payload.get("candidateId")
            or normalized_payload.get("id")
        )
        normalized_fingerprint = _normalize_text(
            normalized_payload.get("resume_fingerprint")
            or normalized_payload.get("resumeFingerprint")
            or normalized_payload.get("fingerprint")
        )
        source_filename = _normalize_text(
            normalized_payload.get("source_filename")
            or normalized_payload.get("sourceFilename")
            or normalized_payload.get("file_name")
            or normalized_payload.get("fileName")
        )
        source_path = _normalize_text(normalized_payload.get("source_path") or normalized_payload.get("sourcePath"))
        source_metadata = normalized_payload.get("source_metadata") or normalized_payload.get("sourceMetadata") or {}
        full_name = _normalize_text(normalized_payload.get("full_name") or normalized_payload.get("fullName") or normalized_payload.get("name"))
        headline = _normalize_text(normalized_payload.get("headline") or normalized_payload.get("title") or normalized_payload.get("role"))
        years_experience = float(
            normalized_payload.get("years_experience")
            or normalized_payload.get("yearsExperience")
            or 0.0
        )
        skills = list(normalized_payload.get("skills") or [])
        companies = list(normalized_payload.get("companies") or [])
        education = list(normalized_payload.get("education") or [])
        projects = list(normalized_payload.get("projects") or [])
        certifications = list(normalized_payload.get("certifications") or [])
        location = _normalize_text(normalized_payload.get("location"))
        summary = _normalize_text(normalized_payload.get("summary"))
        domain_experience = list(normalized_payload.get("domain_experience") or normalized_payload.get("domainExperience") or [])
        raw_resume_text = _normalize_text(normalized_payload.get("raw_resume_text") or normalized_payload.get("rawResumeText"))
        parsed_data = normalized_payload.get("parsed_data") or normalized_payload.get("parsedData") or {}
        embedding_version = _normalize_text(
            normalized_payload.get("embedding_version")
            or normalized_payload.get("embeddingVersion")
            or ""
        )
        vector_version = _normalize_text(
            normalized_payload.get("vector_version")
            or normalized_payload.get("vectorVersion")
            or embedding_version
        )
        qdrant_point_id = _normalize_text(
            normalized_payload.get("qdrant_point_id")
            or normalized_payload.get("qdrantPointId")
            or candidate_id
        )
        if not candidate_id:
            raise APIError("candidate_id is required", status_code=400)
        if not normalized_fingerprint:
            raise APIError("resume_fingerprint is required", status_code=400)

        row = self.get_by_fingerprint(normalized_fingerprint) or self.get_by_candidate_id(candidate_id)
        if not row:
            row = InternalCandidateResumeEntity(
                id=str(uuid4()),
                candidate_id=candidate_id,
                resume_fingerprint=normalized_fingerprint,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                logger.info(
                    "internal_candidate_duplicate_skipped candidate_id=%s fingerprint=%s",
                    candidate_id,
                    normalized_fingerprint,
                )
                row = self.get_by_fingerprint(normalized_fingerprint) or self.get_by_candidate_id(candidate_id)
                if not row:
                    raise

        row.candidate_id = candidate_id
        row.resume_fingerprint = normalized_fingerprint
        row.source_filename = source_filename
        row.source_path = source_path
        row.source_metadata = dict(source_metadata or {})
        row.full_name = full_name
        row.headline = headline
        row.years_experience = years_experience
        row.skills = list(skills or [])
        row.companies = list(companies or [])
        row.education = list(education or [])
        row.projects = list(projects or [])
        row.certifications = list(certifications or [])
        row.location = location
        row.summary = summary
        row.domain_experience = list(domain_experience or [])
        row.raw_resume_text = raw_resume_text
        extra_payload = {
            key: value
            for key, value in normalized_payload.items()
            if key
            not in {
                "candidate_id",
                "candidateId",
                "id",
                "resume_fingerprint",
                "resumeFingerprint",
                "fingerprint",
                "source_filename",
                "sourceFilename",
                "file_name",
                "fileName",
                "source_path",
                "sourcePath",
                "source_metadata",
                "sourceMetadata",
                "full_name",
                "fullName",
                "name",
                "headline",
                "title",
                "role",
                "years_experience",
                "yearsExperience",
                "skills",
                "companies",
                "education",
                "projects",
                "certifications",
                "location",
                "summary",
                "domain_experience",
                "domainExperience",
                "raw_resume_text",
                "rawResumeText",
                "parsed_data",
                "parsedData",
                "embedding_version",
                "embeddingVersion",
                "vector_version",
                "vectorVersion",
                "qdrant_point_id",
                "qdrantPointId",
            }
        }
        row.parsed_data = {**dict(parsed_data or {}), **extra_payload}
        row.embedding_version = embedding_version
        row.vector_version = vector_version
        row.qdrant_point_id = qdrant_point_id
        row.indexed_at = now
        row.updated_at = now
        self.db.flush()
        return row

    def list_recent(self, limit: int = 100) -> list[InternalCandidateResumeEntity]:
        rows = self.db.scalars(
            select(InternalCandidateResumeEntity)
            .order_by(InternalCandidateResumeEntity.updated_at.desc())
            .limit(max(1, limit))
        ).all()
        return list(rows)

    def count(self) -> int:
        count = self.db.scalar(select(func.count()).select_from(InternalCandidateResumeEntity))
        return int(count or 0)


class CandidateFeedbackRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, *, job_id: str, candidate_id: str) -> CandidateFeedbackEntity | None:
        return self.db.scalar(
            select(CandidateFeedbackEntity).where(
                CandidateFeedbackEntity.job_id == job_id,
                CandidateFeedbackEntity.candidate_id == candidate_id,
            )
        )

    def upsert(
        self,
        *,
        job_id: str,
        candidate_id: str,
        feedback: str,
        recruiter_id: str | None = None,
        session_id: str | None = None,
    ) -> CandidateFeedbackEntity:
        feedback = feedback.strip().lower()
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = CandidateFeedbackEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                feedback=feedback,
                recruiter_id=(recruiter_id or "").strip() or None,
                session_id=(session_id or "").strip() or None,
                created_at=now,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                logger.info("candidate_feedback_duplicate_skipped job_id=%s candidate_id=%s", job_id, candidate_id)
                row = self.get(job_id=job_id, candidate_id=candidate_id)
                if not row:
                    raise

        row.feedback = feedback
        row.accepted = feedback == "accept"
        row.rejected = feedback == "reject"
        row.recruiter_id = (recruiter_id or row.recruiter_id or "").strip() or None
        row.session_id = (session_id or row.session_id or "").strip() or None
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[CandidateFeedbackEntity]:
        rows = self.db.scalars(select(CandidateFeedbackEntity).where(CandidateFeedbackEntity.job_id == job_id)).all()
        return list(rows)

    def list_by_job(self, job_id: str) -> list[CandidateFeedbackEntity]:
        rows = self.db.scalars(
            select(CandidateFeedbackEntity).where(CandidateFeedbackEntity.job_id == job_id)
        ).all()
        return list(rows)

    def list_recent_global(self, limit: int = 100) -> list[CandidateFeedbackEntity]:
        rows = self.db.scalars(
            select(CandidateFeedbackEntity)
            .order_by(CandidateFeedbackEntity.updated_at.desc())
            .limit(limit)
        ).all()
        return list(rows)

    def list_all(self) -> list[CandidateFeedbackEntity]:
        rows = self.db.scalars(select(CandidateFeedbackEntity)).all()
        return list(rows)

    def count_for_job(self, job_id: str) -> int:
        count = self.db.scalar(
            select(func.count()).select_from(CandidateFeedbackEntity).where(CandidateFeedbackEntity.job_id == job_id)
        )
        return int(count or 0)

    def count_for_recruiter(self, recruiter_id: str) -> int:
        recruiter_id = (recruiter_id or "").strip()
        if not recruiter_id:
            return 0
        count = self.db.scalar(
            select(func.count()).select_from(CandidateFeedbackEntity).where(CandidateFeedbackEntity.recruiter_id == recruiter_id)
        )
        return int(count or 0)

    def get_learning_summary_for_recruiter(self, recruiter_id: str) -> dict[str, int]:
        recruiter_id = (recruiter_id or "").strip()
        if not recruiter_id:
            return {"feedback_count": 0, "selection_count": 0, "rejection_count": 0}

        row = self.db.execute(
            select(
                func.count().label("feedback_count"),
                func.sum(case((CandidateFeedbackEntity.accepted.is_(True), 1), else_=0)).label("selection_count"),
                func.sum(case((CandidateFeedbackEntity.rejected.is_(True), 1), else_=0)).label("rejection_count"),
            ).where(CandidateFeedbackEntity.recruiter_id == recruiter_id)
        ).one()

        return {
            "feedback_count": int(getattr(row, "feedback_count", 0) or 0),
            "selection_count": int(getattr(row, "selection_count", 0) or 0),
            "rejection_count": int(getattr(row, "rejection_count", 0) or 0),
        }


class RankingExplanationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def store_bulk(self, rows: list[dict[str, float | str]]) -> None:
        cleaned = [
            {
                "id": str(uuid4()),
                "job_id": str(row.get("job_id") or "").strip(),
                "candidate_id": str(row.get("candidate_id") or "").strip(),
                "existing_score": float(row.get("existing_score") or 0.0),
                "recruiter_score": float(row.get("recruiter_score") or 0.0),
                "session_signal": float(row.get("session_signal") or 0.0),
                "final_score": float(row.get("final_score") or 0.0),
                "recruiter_capped": bool(row.get("recruiter_capped") or False),
                "updated_at": datetime.now(timezone.utc),
                "created_at": datetime.now(timezone.utc),
            }
            for row in rows
            if str(row.get("job_id") or "").strip() and str(row.get("candidate_id") or "").strip()
        ]
        if not cleaned:
            return

        table = RankingExplanationEntity.__table__
        dialect_name = getattr(getattr(self.db.get_bind(), "dialect", None), "name", "") or ""
        if dialect_name in {"postgresql", "sqlite"}:
            insert_stmt = pg_insert(table) if dialect_name == "postgresql" else sqlite_insert(table)
            stmt = insert_stmt.values(cleaned)
            stmt = stmt.on_conflict_do_update(
                index_elements=["job_id", "candidate_id"],
                set_={
                    "existing_score": stmt.excluded.existing_score,
                    "recruiter_score": stmt.excluded.recruiter_score,
                    "session_signal": stmt.excluded.session_signal,
                    "final_score": stmt.excluded.final_score,
                    "recruiter_capped": stmt.excluded.recruiter_capped,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            self.db.execute(stmt)
            self.db.flush()
            return

        for row in cleaned:
            existing = self.db.scalar(
                select(RankingExplanationEntity).where(
                    RankingExplanationEntity.job_id == row["job_id"],
                    RankingExplanationEntity.candidate_id == row["candidate_id"],
                )
            )
            if not existing:
                existing = RankingExplanationEntity(
                    id=row["id"],
                    job_id=row["job_id"],
                    candidate_id=row["candidate_id"],
                    existing_score=float(row["existing_score"]),
                    recruiter_score=float(row["recruiter_score"]),
                    session_signal=float(row["session_signal"]),
                    final_score=float(row["final_score"]),
                    recruiter_capped=bool(row["recruiter_capped"]),
                )
                self.db.add(existing)
            else:
                existing.existing_score = float(row["existing_score"])
                existing.recruiter_score = float(row["recruiter_score"])
                existing.session_signal = float(row["session_signal"])
                existing.final_score = float(row["final_score"])
                existing.recruiter_capped = bool(row["recruiter_capped"])
                existing.updated_at = datetime.now(timezone.utc)
        self.db.flush()


class RankingRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        recruiter_id: str | None,
        run_type: str,
        avg_existing_score: float,
        avg_final_score: float,
        avg_recruiter_score: float,
        percent_recruiter_capped: float,
        candidate_count: int,
        drift_delta: float,
    ) -> RankingRunEntity:
        row = RankingRunEntity(
            id=str(uuid4()),
            job_id=job_id,
            recruiter_id=(recruiter_id or "").strip() or None,
            run_type=(run_type or "initial").strip().lower() or "initial",
            avg_existing_score=float(avg_existing_score),
            avg_final_score=float(avg_final_score),
            avg_recruiter_score=float(avg_recruiter_score),
            percent_recruiter_capped=float(percent_recruiter_capped),
            candidate_count=int(candidate_count),
            drift_delta=float(drift_delta),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_for_recruiter(
        self,
        *,
        recruiter_id: str,
        job_id: str | None = None,
        limit: int = 20,
    ) -> list[RankingRunEntity]:
        recruiter_id = (recruiter_id or "").strip()
        if not recruiter_id:
            return []

        stmt = select(RankingRunEntity).where(RankingRunEntity.recruiter_id == recruiter_id)
        if job_id:
            stmt = stmt.where(RankingRunEntity.job_id == job_id)
        rows = self.db.scalars(
            stmt.order_by(RankingRunEntity.created_at.desc()).limit(max(1, limit))
        ).all()
        return list(rows)


class RecruiterPreferenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _dialect_name(self) -> str:
        bind = self.db.get_bind()
        return getattr(getattr(bind, "dialect", None), "name", "") or ""

    def _upsert_weighted_preference(
        self,
        *,
        entity,
        value_field: str,
        recruiter_id: str,
        value: str,
        delta: float,
        track_counts: bool = True,
    ):
        normalized_value = (value or "").strip().lower()
        recruiter_id = (recruiter_id or "").strip()
        if not recruiter_id or not normalized_value:
            return None

        now = datetime.now(timezone.utc)
        table = entity.__table__
        existing = self.db.scalar(
            select(entity).where(
                entity.recruiter_id == recruiter_id,
                getattr(entity, value_field) == normalized_value,
            )
        )
        old_weight = float(getattr(existing, "weight", 0.0) or 0.0)
        if existing and existing.updated_at:
            age_days = max(0.0, (now - existing.updated_at).total_seconds() / 86400.0)
            if age_days > 30:
                decay_multiplier = max(0.75, 0.95 ** (age_days / 30.0))
                old_weight *= decay_multiplier
        updated_weight = (old_weight * 0.9) + (float(delta) * 0.1)
        table_columns = set(table.c.keys())
        insert_values = {
            "id": str(uuid4()),
            "recruiter_id": recruiter_id,
            value_field: normalized_value,
            "weight": max(0.0, updated_weight),
            "updated_at": now,
        }
        if "created_at" in table_columns:
            insert_values["created_at"] = now
        if track_counts:
            if "positive_count" in table_columns:
                insert_values["positive_count"] = int(getattr(existing, "positive_count", 0) or 0) + (1 if delta > 0 else 0)
            if "negative_count" in table_columns:
                insert_values["negative_count"] = int(getattr(existing, "negative_count", 0) or 0) + (1 if delta < 0 else 0)

        dialect_name = self._dialect_name()
        if dialect_name == "postgresql":
            stmt = pg_insert(table).values(**insert_values)
            excluded = stmt.excluded
            if track_counts:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["recruiter_id", value_field],
                    set_={
                        "weight": excluded.weight,
                        "positive_count": excluded.positive_count,
                        "negative_count": excluded.negative_count,
                        "updated_at": now,
                    },
                )
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["recruiter_id", value_field],
                    set_={
                        "weight": excluded.weight,
                        "updated_at": now,
                    },
                )
            self.db.execute(stmt)
            self.db.flush()
            return self.db.scalar(select(entity).where(entity.recruiter_id == recruiter_id, getattr(entity, value_field) == normalized_value))

        if dialect_name == "sqlite":
            stmt = sqlite_insert(table).values(**insert_values)
            excluded = stmt.excluded
            if track_counts:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["recruiter_id", value_field],
                    set_={
                        "weight": excluded.weight,
                        "positive_count": excluded.positive_count,
                        "negative_count": excluded.negative_count,
                        "updated_at": now,
                    },
                )
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["recruiter_id", value_field],
                    set_={
                        "weight": excluded.weight,
                        "updated_at": now,
                    },
                )
            self.db.execute(stmt)
            self.db.flush()
            return self.db.scalar(select(entity).where(entity.recruiter_id == recruiter_id, getattr(entity, value_field) == normalized_value))

        row = existing
        if not row:
            row = entity(
                id=str(uuid4()),
                recruiter_id=recruiter_id,
                **{
                    value_field: normalized_value,
                    "weight": max(0.0, updated_weight),
                    "positive_count": 1 if delta > 0 else 0,
                    "negative_count": 1 if delta < 0 else 0,
                },
            )
            self.db.add(row)
        else:
            row.weight = max(0.0, updated_weight)
            if track_counts:
                row.positive_count = int(row.positive_count or 0) + (1 if delta > 0 else 0)
                row.negative_count = int(row.negative_count or 0) + (1 if delta < 0 else 0)
        row.updated_at = now
        self.db.flush()
        return row

    def upsert_skill_preference(self, *, recruiter_id: str, skill: str, delta: float) -> RecruiterSkillPreferenceEntity | None:
        return self._upsert_weighted_preference(
            entity=RecruiterSkillPreferenceEntity,
            value_field="skill",
            recruiter_id=recruiter_id,
            value=skill,
            delta=delta,
        )

    def upsert_role_preference(self, *, recruiter_id: str, role: str, delta: float) -> RecruiterRolePreferenceEntity | None:
        return self._upsert_weighted_preference(
            entity=RecruiterRolePreferenceEntity,
            value_field="role",
            recruiter_id=recruiter_id,
            value=role,
            delta=delta,
        )

    def list_skill_preferences(self, *, recruiter_id: str, limit: int = 8) -> list[RecruiterSkillPreferenceEntity]:
        rows = self.db.scalars(
            select(RecruiterSkillPreferenceEntity)
            .where(RecruiterSkillPreferenceEntity.recruiter_id == recruiter_id)
            .order_by(
                RecruiterSkillPreferenceEntity.weight.desc(),
                RecruiterSkillPreferenceEntity.positive_count.desc(),
                RecruiterSkillPreferenceEntity.updated_at.desc(),
            )
            .limit(max(1, limit))
        ).all()
        return list(rows)

    def list_role_preferences(self, *, recruiter_id: str, limit: int = 6) -> list[RecruiterRolePreferenceEntity]:
        rows = self.db.scalars(
            select(RecruiterRolePreferenceEntity)
            .where(RecruiterRolePreferenceEntity.recruiter_id == recruiter_id)
            .order_by(
                RecruiterRolePreferenceEntity.weight.desc(),
                RecruiterRolePreferenceEntity.positive_count.desc(),
                RecruiterRolePreferenceEntity.updated_at.desc(),
            )
            .limit(max(1, limit))
        ).all()
        return list(rows)

    def upsert_experience_preference(self, *, recruiter_id: str, experience_bucket: str, delta: float) -> RecruiterExperiencePreferenceEntity | None:
        return self._upsert_weighted_preference(
            entity=RecruiterExperiencePreferenceEntity,
            value_field="experience_bucket",
            recruiter_id=recruiter_id,
            value=experience_bucket,
            delta=delta,
            track_counts=False,
        )

    def list_experience_preferences(self, *, recruiter_id: str, limit: int = 4) -> list[RecruiterExperiencePreferenceEntity]:
        rows = self.db.scalars(
            select(RecruiterExperiencePreferenceEntity)
            .where(RecruiterExperiencePreferenceEntity.recruiter_id == recruiter_id)
            .order_by(
                RecruiterExperiencePreferenceEntity.weight.desc(),
                RecruiterExperiencePreferenceEntity.updated_at.desc(),
            )
            .limit(max(1, limit))
        ).all()
        return list(rows)

    def count_silent_learning_events(self, recruiter_id: str) -> int:
        recruiter_id = (recruiter_id or "").strip()
        if not recruiter_id:
            return 0
        count = self.db.scalar(
            select(func.count())
            .select_from(OutreachEventEntity)
            .join(JobEntity, JobEntity.id == OutreachEventEntity.job_id)
            .join(CompanyEntity, CompanyEntity.id == JobEntity.company_id)
            .where(
                OutreachEventEntity.learning_applied.is_(True),
                OutreachEventEntity.responded_at.is_(None),
                OutreachEventEntity.status.in_(("sent", "delivered")),
                CompanyEntity.user_id == recruiter_id,
            )
        )
        return int(count or 0)


class ScoringProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, *, job_id: str) -> ScoringProfileEntity | None:
        return self.db.scalar(select(ScoringProfileEntity).where(ScoringProfileEntity.job_id == job_id))

    def get_or_create(self, *, job_id: str) -> ScoringProfileEntity:
        row = self.get(job_id=job_id)
        if row:
            return row

        row = ScoringProfileEntity(id=str(uuid4()), job_id=job_id)
        try:
            self.db.add(row)
            self.db.flush()
            return row
        except IntegrityError:
            self.db.rollback()
            existing = self.get(job_id=job_id)
            if existing:
                return existing
            raise

    def apply_feedback_adjustment(self, *, job_id: str, feedback: str) -> ScoringProfileEntity:
        row = self.get_or_create(job_id=job_id)
        feedback = feedback.strip().lower()
        feedback_count = CandidateFeedbackRepository(self.db).count_for_job(job_id)
        alpha = max(0.01, min(1.0, RLHF_SMOOTHING_ALPHA))

        target_pdl = row.weight_pdl
        target_semantic = row.weight_semantic
        target_skill = row.weight_skill
        target_recency = row.weight_recency

        # Conservative target updates; smoothed by alpha below.
        if feedback == "accept":
            target_semantic = min(0.65, row.weight_semantic + 0.02)
            target_skill = min(0.30, row.weight_skill + 0.02)
            target_pdl = max(0.10, row.weight_pdl - 0.02)
        elif feedback == "reject":
            target_semantic = max(0.25, row.weight_semantic - 0.02)
            target_recency = min(0.20, row.weight_recency + 0.02)
            target_pdl = min(0.50, row.weight_pdl + 0.01)

        row.weight_pdl = ((1 - alpha) * row.weight_pdl) + (alpha * target_pdl)
        row.weight_semantic = ((1 - alpha) * row.weight_semantic) + (alpha * target_semantic)
        row.weight_skill = ((1 - alpha) * row.weight_skill) + (alpha * target_skill)
        row.weight_recency = ((1 - alpha) * row.weight_recency) + (alpha * target_recency)

        total = row.weight_pdl + row.weight_semantic + row.weight_skill + row.weight_recency
        if total > 0:
            row.weight_pdl = row.weight_pdl / total
            row.weight_semantic = row.weight_semantic / total
            row.weight_skill = row.weight_skill / total
            row.weight_recency = row.weight_recency / total
        row.feedback_bias = max(
            RLHF_MIN_FEEDBACK_BIAS,
            RLHF_BASE_FEEDBACK_BIAS / max(1.0, math.sqrt(max(1, feedback_count))),
        )

        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return row


class CandidateSelectionSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_job(self, job_id: str) -> CandidateSelectionSessionEntity | None:
        return self.db.scalar(
            select(CandidateSelectionSessionEntity)
            .where(CandidateSelectionSessionEntity.job_id == job_id)
            .order_by(CandidateSelectionSessionEntity.created_at.desc())
        )

    def create(
        self,
        *,
        job_id: str,
        candidate_pool_snapshot: list[dict],
        batch_plan: list[list[str]],
        batch_size: int = 2,
        total_batches: int = 3,
    ) -> CandidateSelectionSessionEntity:
        row = CandidateSelectionSessionEntity(
            id=str(uuid4()),
            job_id=job_id,
            status="active",
            current_batch_index=0,
            batch_size=batch_size,
            total_batches=total_batches,
            candidate_pool_snapshot=candidate_pool_snapshot,
            batch_plan=batch_plan,
            selected_candidate_ids=[],
            rejected_candidate_ids=[],
            batch_history=[],
            selection_analysis={},
            final_candidate_snapshot=[],
        )
        try:
            self.db.add(row)
            self.db.flush()
            return row
        except IntegrityError:
            existing = self.get_by_job(job_id)
            if existing:
                logger.info("candidate_selection_duplicate_skipped job_id=%s", job_id)
                self.db.rollback()
                return existing
            raise

    def get_or_create(
        self,
        *,
        job_id: str,
        candidate_pool_snapshot: list[dict] | None = None,
        batch_plan: list[list[str]] | None = None,
        batch_size: int = 2,
        total_batches: int = 3,
    ) -> tuple[CandidateSelectionSessionEntity, bool]:
        existing = self.get_by_job(job_id)
        if existing:
            return existing, False
        if candidate_pool_snapshot is None or batch_plan is None:
            raise APIError("candidate selection session is not initialized", status_code=409)
        return (
            self.create(
                job_id=job_id,
                candidate_pool_snapshot=candidate_pool_snapshot,
                batch_plan=batch_plan,
                batch_size=batch_size,
                total_batches=total_batches,
            ),
            True,
        )

    def mark_selection(
        self,
        row: CandidateSelectionSessionEntity,
        *,
        selected_candidate_id: str,
        rejected_candidate_ids: list[str],
        batch_index: int,
        history_entry: dict,
    ) -> CandidateSelectionSessionEntity:
        selected_ids = [str(candidate_id).strip() for candidate_id in (row.selected_candidate_ids or []) if str(candidate_id).strip()]
        rejected_ids = [str(candidate_id).strip() for candidate_id in (row.rejected_candidate_ids or []) if str(candidate_id).strip()]

        if selected_candidate_id and selected_candidate_id not in selected_ids:
            selected_ids.append(selected_candidate_id)
        for candidate_id in rejected_candidate_ids:
            if candidate_id and candidate_id not in rejected_ids:
                rejected_ids.append(candidate_id)

        history = list(row.batch_history or [])
        history.append(history_entry)

        row.selected_candidate_ids = selected_ids
        row.rejected_candidate_ids = rejected_ids
        row.batch_history = history
        row.current_batch_index = max(0, int(batch_index))
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return row

    def complete(
        self,
        row: CandidateSelectionSessionEntity,
        *,
        selection_analysis: dict,
        final_candidate_snapshot: list[dict],
    ) -> CandidateSelectionSessionEntity:
        row.status = "completed"
        row.selection_analysis = selection_analysis
        row.final_candidate_snapshot = final_candidate_snapshot
        row.completed_at = datetime.now(timezone.utc)
        row.updated_at = row.completed_at
        self.db.flush()
        return row


class ATSExportRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, *, job_id: str, candidate_id: str, provider: str) -> ATSExportEntity | None:
        row = self.db.scalar(
            select(ATSExportEntity).where(
                ATSExportEntity.job_id == job_id,
                ATSExportEntity.candidate_id == candidate_id,
                ATSExportEntity.provider == provider,
            ).order_by(ATSExportEntity.exported_at.desc())
        )
        if row:
            return row

        rows = self.db.scalars(
            select(ATSExportEntity).where(
                ATSExportEntity.job_id == job_id,
                ATSExportEntity.provider == provider,
            ).order_by(ATSExportEntity.exported_at.desc())
        ).all()
        for item in rows:
            candidate_ids = [str(candidate).strip() for candidate in (item.candidate_ids or []) if str(candidate).strip()]
            if candidate_id in candidate_ids:
                return item
        return None

    def create(
        self,
        *,
        job_id: str,
        candidate_id: str | None = None,
        candidate_ids: list[str],
        provider: str,
        status: str,
        external_reference: str,
        error: str = "",
        response_payload: dict,
    ) -> ATSExportEntity:
        normalized_candidate_id = (candidate_id or "").strip() or None
        normalized_candidate_ids = [str(cid).strip() for cid in candidate_ids if str(cid).strip()]
        if normalized_candidate_id and normalized_candidate_id not in normalized_candidate_ids:
            normalized_candidate_ids = [normalized_candidate_id, *normalized_candidate_ids]
        row = ATSExportEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=normalized_candidate_id,
            candidate_ids=normalized_candidate_ids,
            provider=provider,
            status=status,
            external_reference=external_reference,
            error=error,
            response_payload=response_payload,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def create_pending(
        self,
        *,
        job_id: str,
        candidate_id: str,
        candidate_ids: list[str],
        provider: str,
    ) -> tuple[ATSExportEntity, bool]:
        row = ATSExportEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=candidate_id,
            candidate_ids=[str(cid).strip() for cid in candidate_ids if str(cid).strip()] or [candidate_id],
            provider=provider,
            status="sending",
            external_reference="",
            error="",
            response_payload={},
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            return row, True
        except IntegrityError:
            existing = self.get(job_id=job_id, candidate_id=candidate_id, provider=provider)
            if existing:
                return existing, False
            raise

    def list_retryable(self, *, status: str | None = None, limit: int = 100) -> list[ATSExportEntity]:
        stmt = select(ATSExportEntity).where(ATSExportEntity.status.in_(("failed", "sending")))
        if status:
            stmt = stmt.where(ATSExportEntity.status == status)
        rows = self.db.scalars(stmt.order_by(ATSExportEntity.exported_at.asc()).limit(limit)).all()
        return list(rows)

    def mark_sent(self, row: ATSExportEntity, *, external_reference: str, response_payload: dict) -> ATSExportEntity:
        row.status = "sent"
        row.external_reference = external_reference
        row.error = ""
        row.response_payload = response_payload
        row.exported_at = datetime.now(timezone.utc)
        self.db.flush()
        return row

    def mark_failed(
        self,
        row: ATSExportEntity,
        *,
        error: str,
        response_payload: dict | None = None,
        external_reference: str = "",
    ) -> ATSExportEntity:
        row.status = "failed"
        row.external_reference = external_reference or row.external_reference or ""
        row.error = error
        row.response_payload = response_payload or {"error": error}
        row.exported_at = datetime.now(timezone.utc)
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[ATSExportEntity]:
        rows = self.db.scalars(select(ATSExportEntity).where(ATSExportEntity.job_id == job_id)).all()
        return list(rows)


class OutreachEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, event_id: str) -> OutreachEventEntity | None:
        normalized = (event_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(
            select(OutreachEventEntity).where(
                OutreachEventEntity.id == normalized,
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
        )

    def get(self, *, job_id: str, candidate_id: str) -> OutreachEventEntity | None:
        row = self.db.scalar(
            select(OutreachEventEntity)
            .where(
                OutreachEventEntity.job_id == job_id,
                OutreachEventEntity.candidate_id == candidate_id,
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
            .order_by(OutreachEventEntity.created_at.desc())
        )
        if row and not getattr(row, "company_id", "").strip():
            job = JobRepository(self.db).get(job_id)
            if job:
                row.company_id = job.company_id
                self.db.flush()
        return row

    def get_by_provider_message_id(self, provider_message_id: str) -> OutreachEventEntity | None:
        if not provider_message_id:
            return None
        return self.db.scalar(
            select(OutreachEventEntity).where(
                OutreachEventEntity.provider_message_id == provider_message_id,
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
        )

    def claim_outreach_for_sending(
        self,
        *,
        job_id: str,
        candidate_id: str,
        provider: str | None = None,
        to_email: str = "",
        subject: str = "",
        body: str = "",
    ) -> OutreachEventEntity | None:
        candidate_id = (candidate_id or "").strip()
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        ensure_candidate_profile(self.db, job_id, candidate_id)

        row = self.get(job_id=job_id, candidate_id=candidate_id)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                source_app=ADAM_SOURCE_APP,
                job_id=job_id,
                company_id=job.company_id,
                candidate_id=candidate_id,
                provider=provider or "sendgrid",
                to_email=to_email,
                subject=subject,
                body=body,
                status="queued",
                attempt_count=0,
                follow_up_count=0,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                row = self.get(job_id=job_id, candidate_id=candidate_id)
                if not row:
                    raise
        row.company_id = job.company_id

        now = datetime.now(timezone.utc)
        stmt = (
            update(OutreachEventEntity)
            .where(
                OutreachEventEntity.job_id == job_id,
                OutreachEventEntity.candidate_id == candidate_id,
                OutreachEventEntity.provider_message_id.is_(None),
                OutreachEventEntity.status.in_(("queued", "failed")),
            )
            .values(
                source_app=ADAM_SOURCE_APP,
                provider=provider or row.provider,
                to_email=to_email or row.to_email,
                subject=subject or row.subject,
                body=body or row.body,
                status="sending",
                last_error="",
                attempt_count=func.coalesce(OutreachEventEntity.attempt_count, 0) + 1,
                company_id=job.company_id,
                updated_at=now,
            )
            .returning(OutreachEventEntity)
        )
        return self.db.scalar(stmt)

    def upsert(
        self,
        *,
        job_id: str,
        candidate_id: str,
        provider: str,
        to_email: str,
        subject: str,
        body: str,
        status: str,
        last_error: str = "",
        sent_at: datetime | None = None,
        next_follow_up_at: datetime | None = None,
        provider_message_id: str | None = None,
        increment_follow_up: bool = False,
    ) -> OutreachEventEntity:
        candidate_id = (candidate_id or "").strip()
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                source_app=ADAM_SOURCE_APP,
                job_id=job_id,
                company_id=job.company_id,
                candidate_id=candidate_id,
                attempt_count=0,
                follow_up_count=0,
            )
            self.db.add(row)
            self.db.flush()

        row.provider = provider
        row.source_app = ADAM_SOURCE_APP
        row.to_email = to_email
        row.subject = subject
        row.body = body
        row.status = status
        row.last_error = last_error
        row.attempt_count = int(row.attempt_count or 0) + 1
        if increment_follow_up:
            row.follow_up_count = int(row.follow_up_count or 0) + 1
        if provider_message_id is not None:
            row.provider_message_id = provider_message_id
        if sent_at:
            row.sent_at = sent_at
            row.last_sent_at = sent_at
            row.last_contacted_at = sent_at
        row.next_follow_up_at = next_follow_up_at
        row.company_id = job.company_id
        row.updated_at = now
        self.db.flush()
        return row

    def upsert_response(
        self,
        *,
        job_id: str,
        candidate_id: str,
        provider: str,
        message_text: str,
        resume_url: str = "",
        status: str = "responded",
        provider_message_id: str | None = None,
        received_at: datetime | None = None,
        last_error: str = "",
    ) -> OutreachEventEntity:
        candidate_id = (candidate_id or "").strip()
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                source_app=ADAM_SOURCE_APP,
                job_id=job_id,
                company_id=job.company_id,
                candidate_id=candidate_id,
                attempt_count=0,
                follow_up_count=0,
            )
            self.db.add(row)
            self.db.flush()
        row.company_id = job.company_id

        row.provider = provider
        row.source_app = ADAM_SOURCE_APP
        row.message_text = message_text.strip()
        row.resume_url = resume_url.strip()
        row.status = status
        if provider_message_id and not (row.provider_message_id or "").strip():
            row.provider_message_id = provider_message_id
        row.last_error = last_error
        row.last_contacted_at = received_at or now
        row.responded_at = received_at or now
        row.company_id = job.company_id
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[OutreachEventEntity]:
        rows = self.db.scalars(
            select(OutreachEventEntity).where(
                OutreachEventEntity.job_id == job_id,
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
        ).all()
        return list(rows)

    def list_due_follow_ups(self, *, now: datetime, max_follow_up_count: int) -> list[OutreachEventEntity]:
        """Return outreach events that are due for a follow-up and haven't exceeded max attempts."""
        rows = self.db.scalars(
            select(OutreachEventEntity).where(
                OutreachEventEntity.status.in_(("sent", "follow_up_sent")),
                OutreachEventEntity.next_follow_up_at <= now,
                OutreachEventEntity.follow_up_count < max_follow_up_count,
                OutreachEventEntity.to_email != "",
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
        ).all()
        return list(rows)

    def list_replied(self, *, job_id: str | None = None) -> list[OutreachEventEntity]:
        stmt = select(OutreachEventEntity).where(
            OutreachEventEntity.status == "replied",
            OutreachEventEntity.source_app == ADAM_SOURCE_APP,
        )
        if job_id:
            stmt = stmt.where(OutreachEventEntity.job_id == job_id)
        rows = self.db.scalars(stmt).all()
        return list(rows)

    def list_due_follow_ups_locked(self, *, now: datetime, max_follow_up_count: int) -> list[OutreachEventEntity]:
        stmt = (
            select(OutreachEventEntity)
            .where(
                OutreachEventEntity.status.in_(("sent", "follow_up_sent")),
                OutreachEventEntity.next_follow_up_at <= now,
                OutreachEventEntity.follow_up_count < max_follow_up_count,
                OutreachEventEntity.to_email != "",
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
            .with_for_update(skip_locked=True)
        )
        rows = self.db.scalars(stmt).all()
        return list(rows)

    def list_stale_for_learning_locked(
        self,
        *,
        now: datetime,
        max_follow_up_count: int,
        limit: int,
    ) -> list[OutreachEventEntity]:
        stmt = (
            select(OutreachEventEntity)
            .where(
                OutreachEventEntity.status.in_(("sent", "delivered")),
                OutreachEventEntity.follow_up_count >= max_follow_up_count,
                OutreachEventEntity.responded_at.is_(None),
                OutreachEventEntity.learning_applied.is_(False),
                OutreachEventEntity.to_email != "",
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
                or_(
                    OutreachEventEntity.next_follow_up_at.is_(None),
                    OutreachEventEntity.next_follow_up_at <= now,
                ),
            )
            .order_by(OutreachEventEntity.updated_at.asc(), OutreachEventEntity.created_at.asc())
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        )
        rows = self.db.scalars(stmt).all()
        return list(rows)

    def list_recent(self, *, limit: int = 500) -> list[OutreachEventEntity]:
        rows = self.db.scalars(
            select(OutreachEventEntity)
            .where(OutreachEventEntity.source_app == ADAM_SOURCE_APP)
            .order_by(OutreachEventEntity.updated_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def find_latest_by_email(self, email: str) -> OutreachEventEntity | None:
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        return self.db.scalar(
            select(OutreachEventEntity)
            .where(
                OutreachEventEntity.to_email == normalized,
                OutreachEventEntity.source_app == ADAM_SOURCE_APP,
            )
            .order_by(OutreachEventEntity.created_at.desc())
        )


class NotificationWorkflowTokenRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _normalize_source_app(source_app: str | None) -> str:
        return (source_app or "dashboard").strip().lower() or "dashboard"

    def get_by_token(self, token: str, *, source_app: str = "dashboard") -> NotificationWorkflowTokenEntity | None:
        normalized = (token or "").strip()
        if not normalized:
            return None
        return self.db.scalar(
            select(NotificationWorkflowTokenEntity).where(
                NotificationWorkflowTokenEntity.token == normalized,
                NotificationWorkflowTokenEntity.source_app == self._normalize_source_app(source_app),
            )
        )

    def get_active_by_candidate(
        self,
        *,
        job_id: str,
        candidate_id: str,
        source_app: str = "dashboard",
        token_type: str = "",
    ) -> NotificationWorkflowTokenEntity | None:
        normalized_job_id = (job_id or "").strip()
        normalized_candidate_id = (candidate_id or "").strip()
        normalized_source_app = self._normalize_source_app(source_app)
        normalized_token_type = (token_type or "").strip().lower()
        if not normalized_job_id or not normalized_candidate_id:
            return None
        conditions = [
            NotificationWorkflowTokenEntity.job_id == normalized_job_id,
            NotificationWorkflowTokenEntity.candidate_id == normalized_candidate_id,
            NotificationWorkflowTokenEntity.source_app == normalized_source_app,
            NotificationWorkflowTokenEntity.is_active.is_(True),
        ]
        if normalized_token_type:
            conditions.append(NotificationWorkflowTokenEntity.token_type == normalized_token_type)
        conditions.append(
            or_(
                NotificationWorkflowTokenEntity.expires_at.is_(None),
                NotificationWorkflowTokenEntity.expires_at > datetime.now(timezone.utc),
            )
        )
        return self.db.scalar(select(NotificationWorkflowTokenEntity).where(*conditions))

    def create(
        self,
        *,
        job_id: str,
        candidate_id: str,
        workflow_name: str,
        token: str | None = None,
        payload: dict | None = None,
        token_type: str = "",
        is_active: bool = True,
        status: str | None = None,
        expires_at: datetime | None = None,
        source_app: str = "dashboard",
    ) -> NotificationWorkflowTokenEntity:
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)
        token_value = (token or "").strip()
        if not token_value:
            raise APIError("token is required", status_code=400)
        normalized_source_app = self._normalize_source_app(source_app)
        normalized_token_type = (token_type or workflow_name or "").strip().lower()
        normalized_workflow_name = (workflow_name or normalized_token_type or "").strip()
        normalized_status = (status or "").strip().lower() or ("active" if is_active else "consumed")
        normalized_is_active = bool(is_active) if status is None else normalized_status == "active"
        row = NotificationWorkflowTokenEntity(
            id=str(uuid4()),
            source_app=normalized_source_app,
            job_id=job_id,
            candidate_id=(candidate_id or "").strip(),
            token_type=normalized_token_type,
            workflow_name=normalized_workflow_name,
            token=token_value,
            is_active=normalized_is_active,
            status=normalized_status,
            payload=dict(payload or {}),
            expires_at=expires_at,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def mark_consumed(self, token: str, *, source_app: str = "dashboard") -> NotificationWorkflowTokenEntity | None:
        row = self.get_by_token(token, source_app=source_app)
        if not row:
            return None
        row.status = "consumed"
        row.is_active = False
        row.consumed_at = datetime.now(timezone.utc)
        row.updated_at = row.consumed_at
        self.db.flush()
        return row


class CandidateLifecycleEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        company_id: str,
        candidate_id: str,
        from_status: str,
        to_status: str,
        source: str = "system",
        actor_id: str | None = None,
        transition_key: str,
        event_metadata: dict | None = None,
    ) -> CandidateLifecycleEventEntity:
        row = CandidateLifecycleEventEntity(
            id=str(uuid4()),
            job_id=job_id,
            company_id=company_id,
            candidate_id=candidate_id,
            from_status=(from_status or "").strip().lower(),
            to_status=(to_status or "").strip().lower(),
            source=(source or "system").strip().lower(),
            actor_id=actor_id,
            transition_key=(transition_key or "").strip(),
            event_metadata=dict(event_metadata or {}),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_for_candidate(self, *, job_id: str, candidate_id: str, limit: int = 100) -> list[CandidateLifecycleEventEntity]:
        rows = self.db.scalars(
            select(CandidateLifecycleEventEntity)
            .where(
                CandidateLifecycleEventEntity.job_id == job_id,
                CandidateLifecycleEventEntity.candidate_id == candidate_id,
            )
            .order_by(CandidateLifecycleEventEntity.created_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)


class NotificationEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_key(self, notification_key: str) -> NotificationEventEntity | None:
        normalized = (notification_key or "").strip()
        if not normalized:
            return None
        return self.db.scalar(
            select(NotificationEventEntity).where(NotificationEventEntity.notification_key == normalized)
        )

    def upsert(
        self,
        *,
        notification_key: str,
        job_id: str | None = None,
        company_id: str | None = None,
        candidate_id: str | None = None,
        actor_id: str | None = None,
        recipient_type: str = "recruiter",
        recipient: str = "",
        channel: str = "slack",
        title: str = "",
        body: str = "",
        status: str = "queued",
        notification_type: str = "",
        notification_metadata: dict | None = None,
        delivery_reference: str = "",
    ) -> NotificationEventEntity:
        row = self.get_by_key(notification_key)
        now = datetime.now(timezone.utc)
        if not row:
            row = NotificationEventEntity(
                id=str(uuid4()),
                notification_key=(notification_key or "").strip(),
            )
            self.db.add(row)
            self.db.flush()
        row.job_id = job_id
        row.company_id = company_id
        row.candidate_id = candidate_id
        row.actor_id = actor_id
        row.recipient_type = (recipient_type or "recruiter").strip().lower()
        row.recipient = (recipient or "").strip()
        row.channel = (channel or "slack").strip().lower()
        row.title = (title or "").strip()
        row.body = (body or "").strip()
        row.status = (status or "queued").strip().lower()
        row.notification_type = (notification_type or "").strip().lower()
        row.notification_metadata = dict(notification_metadata or {})
        row.delivery_reference = (delivery_reference or "").strip()
        row.updated_at = now
        if row.status == "delivered":
            row.delivered_at = now
        elif row.status in {"failed", "error"}:
            row.failed_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str, limit: int = 200) -> list[NotificationEventEntity]:
        rows = self.db.scalars(
            select(NotificationEventEntity)
            .where(NotificationEventEntity.job_id == job_id)
            .order_by(NotificationEventEntity.created_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def list_recent(self, limit: int = 100, *, unread_only: bool = False) -> list[NotificationEventEntity]:
        stmt = select(NotificationEventEntity)
        if unread_only:
            stmt = stmt.where(NotificationEventEntity.is_read.is_(False))
        rows = self.db.scalars(
            stmt.order_by(NotificationEventEntity.created_at.desc()).limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def mark_read(self, notification_key: str) -> NotificationEventEntity | None:
        row = self.get_by_key(notification_key)
        if not row:
            return None
        now = datetime.now(timezone.utc)
        row.is_read = True
        row.read_at = now
        row.updated_at = now
        self.db.flush()
        return row


class AutomationJobRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, automation_key: str) -> AutomationJobEntity | None:
        normalized = (automation_key or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(AutomationJobEntity).where(AutomationJobEntity.automation_key == normalized))

    def upsert(
        self,
        *,
        automation_key: str,
        automation_type: str,
        job_id: str | None = None,
        candidate_id: str | None = None,
        scheduled_at: datetime | None = None,
        payload: dict | None = None,
        status: str = "queued",
        max_attempts: int = 3,
    ) -> AutomationJobEntity:
        row = self.get_by_key(automation_key)
        now = datetime.now(timezone.utc)
        if not row:
            row = AutomationJobEntity(
                id=str(uuid4()),
                automation_key=(automation_key or "").strip(),
            )
            self.db.add(row)
            self.db.flush()
        row.job_id = (job_id or "").strip() or None
        row.candidate_id = (candidate_id or "").strip() or None
        row.automation_type = (automation_type or "").strip().lower()
        row.status = (status or "queued").strip().lower()
        row.scheduled_at = scheduled_at or row.scheduled_at or now
        row.max_attempts = max(1, int(max_attempts or 1))
        row.automation_payload = dict(payload or {})
        row.updated_at = now
        self.db.flush()
        return row

    def list_due(self, *, as_of: datetime | None = None, limit: int = 100) -> list[AutomationJobEntity]:
        when = as_of or datetime.now(timezone.utc)
        rows = self.db.scalars(
            select(AutomationJobEntity)
            .where(AutomationJobEntity.status.in_(["queued", "retryable"]), AutomationJobEntity.scheduled_at <= when)
            .order_by(AutomationJobEntity.scheduled_at.asc(), AutomationJobEntity.created_at.asc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def list_recent(self, *, limit: int = 100) -> list[AutomationJobEntity]:
        rows = self.db.scalars(
            select(AutomationJobEntity)
            .order_by(AutomationJobEntity.created_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def mark_started(self, row: AutomationJobEntity) -> AutomationJobEntity:
        now = datetime.now(timezone.utc)
        row.status = "running"
        row.started_at = row.started_at or now
        row.updated_at = now
        row.attempt_count = int(row.attempt_count or 0) + 1
        self.db.flush()
        return row

    def mark_completed(self, row: AutomationJobEntity, *, status: str = "completed") -> AutomationJobEntity:
        now = datetime.now(timezone.utc)
        row.status = status
        row.completed_at = now
        row.updated_at = now
        self.db.flush()
        return row

    def mark_failed(self, row: AutomationJobEntity, *, error: str) -> AutomationJobEntity:
        now = datetime.now(timezone.utc)
        row.status = "failed" if int(row.attempt_count or 0) >= int(row.max_attempts or 1) else "retryable"
        row.last_error = error[:4000]
        row.updated_at = now
        self.db.flush()
        return row


class RecruiterNoteRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        body: str,
        candidate_id: str | None = None,
        recruiter_id: str | None = None,
        note_type: str = "note",
        metadata: dict | None = None,
    ) -> RecruiterNoteEntity:
        row = RecruiterNoteEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=(candidate_id or "").strip() or None,
            recruiter_id=(recruiter_id or "").strip() or None,
            note_type=(note_type or "note").strip().lower(),
            body=(body or "").strip(),
            metadata_json=dict(metadata or {}),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_for_job(self, job_id: str, *, candidate_id: str | None = None, limit: int = 100) -> list[RecruiterNoteEntity]:
        stmt = select(RecruiterNoteEntity).where(RecruiterNoteEntity.job_id == job_id)
        if candidate_id:
            stmt = stmt.where(RecruiterNoteEntity.candidate_id == candidate_id)
        rows = self.db.scalars(stmt.order_by(RecruiterNoteEntity.created_at.desc()).limit(max(1, int(limit)))).all()
        return list(rows)


class RecruiterTaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        title: str,
        body: str = "",
        candidate_id: str | None = None,
        recruiter_id: str | None = None,
        status: str = "open",
        priority: str = "normal",
        due_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> RecruiterTaskEntity:
        row = RecruiterTaskEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=(candidate_id or "").strip() or None,
            recruiter_id=(recruiter_id or "").strip() or None,
            title=(title or "").strip(),
            body=(body or "").strip(),
            status=(status or "open").strip().lower(),
            priority=(priority or "normal").strip().lower(),
            due_at=due_at,
            metadata_json=dict(metadata or {}),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_for_job(self, job_id: str, *, status: str | None = None, limit: int = 100) -> list[RecruiterTaskEntity]:
        stmt = select(RecruiterTaskEntity).where(RecruiterTaskEntity.job_id == job_id)
        if status:
            stmt = stmt.where(RecruiterTaskEntity.status == status)
        rows = self.db.scalars(stmt.order_by(RecruiterTaskEntity.created_at.desc()).limit(max(1, int(limit)))).all()
        return list(rows)

    def mark_done(self, task_id: str) -> RecruiterTaskEntity | None:
        row = self.db.scalar(select(RecruiterTaskEntity).where(RecruiterTaskEntity.id == task_id))
        if not row:
            return None
        now = datetime.now(timezone.utc)
        row.status = "done"
        row.completed_at = now
        row.updated_at = now
        self.db.flush()
        return row


class InterviewEvaluationRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert(
        self,
        *,
        job_id: str,
        candidate_id: str,
        stage_name: str,
        summary: str = "",
        recommendation: str = "",
        interviewer_id: str | None = None,
        competency_scores: dict | None = None,
        notes: str = "",
        metadata: dict | None = None,
        status: str = "draft",
    ) -> InterviewEvaluationEntity:
        row = self.db.scalar(
            select(InterviewEvaluationEntity).where(
                InterviewEvaluationEntity.job_id == job_id,
                InterviewEvaluationEntity.candidate_id == candidate_id,
                InterviewEvaluationEntity.stage_name == stage_name,
            )
        )
        now = datetime.now(timezone.utc)
        if not row:
            row = InterviewEvaluationEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                stage_name=(stage_name or "screen").strip().lower(),
            )
            self.db.add(row)
            self.db.flush()
        row.interviewer_id = (interviewer_id or row.interviewer_id or "").strip() or None
        row.status = (status or "draft").strip().lower()
        row.summary = (summary or "").strip()
        row.recommendation = (recommendation or "").strip().lower()
        row.competency_scores = dict(competency_scores or {})
        row.notes = (notes or "").strip()
        row.metadata_json = dict(metadata or {})
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_candidate(self, *, job_id: str, candidate_id: str, limit: int = 20) -> list[InterviewEvaluationEntity]:
        rows = self.db.scalars(
            select(InterviewEvaluationEntity)
            .where(
                InterviewEvaluationEntity.job_id == job_id,
                InterviewEvaluationEntity.candidate_id == candidate_id,
            )
            .order_by(InterviewEvaluationEntity.updated_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)


class InboundEmailRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_svix_id(self, svix_id: str) -> InboundEmailReplyEntity | None:
        normalized = (svix_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(InboundEmailReplyEntity).where(InboundEmailReplyEntity.svix_id == normalized))

    def get_by_email_id(self, email_id: str) -> InboundEmailReplyEntity | None:
        normalized = (email_id or "").strip()
        if not normalized:
            return None
        return self.db.scalar(select(InboundEmailReplyEntity).where(InboundEmailReplyEntity.email_id == normalized))

    def list_for_candidate(self, *, job_id: str, candidate_id: str, limit: int = 100) -> list[InboundEmailReplyEntity]:
        rows = self.db.scalars(
            select(InboundEmailReplyEntity)
            .where(
                InboundEmailReplyEntity.job_id == job_id,
                InboundEmailReplyEntity.candidate_id == candidate_id,
            )
            .order_by(InboundEmailReplyEntity.received_at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return list(rows)

    def create_or_get(
        self,
        *,
        svix_id: str,
        event_type: str,
        email_id: str,
        provider_message_id: str,
        company_id: str | None = None,
        sender_email: str,
        sender_name: str,
        subject: str,
        body_text: str,
        body_html: str,
        received_at: datetime,
        webhook_created_at: datetime | None,
        raw_payload: dict,
    ) -> tuple[InboundEmailReplyEntity, bool]:
        existing = self.get_by_svix_id(svix_id)
        if existing:
            return existing, False

        normalized_company_id = (company_id or "").strip() or None

        row = InboundEmailReplyEntity(
            id=str(uuid4()),
            svix_id=svix_id,
            event_type=event_type,
            email_id=email_id,
            provider_message_id=provider_message_id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            webhook_created_at=webhook_created_at,
            company_id=normalized_company_id,
            raw_payload=raw_payload,
            processing_status="received",
            match_status="unmatched",
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            return row, True
        except IntegrityError:
            existing = self.get_by_svix_id(svix_id)
            if existing:
                return existing, False
            raise

    def add_attachment(
        self,
        *,
        reply_id: str,
        provider_attachment_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_path: str,
        public_url: str,
        sha256: str,
    ) -> InboundEmailAttachmentEntity:
        row = InboundEmailAttachmentEntity(
            id=str(uuid4()),
            reply_id=reply_id,
            provider_attachment_id=provider_attachment_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            public_url=public_url,
            sha256=sha256,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def mark_processed(
        self,
        row: InboundEmailReplyEntity,
        *,
        processing_status: str,
        match_status: str,
        attachment_count: int = 0,
        processing_error: str = "",
        candidate_id: str | None = None,
        job_id: str | None = None,
        outreach_event_id: str | None = None,
    ) -> InboundEmailReplyEntity:
        job = JobRepository(self.db).get(job_id) if job_id else None
        row.processing_status = processing_status
        row.match_status = match_status
        row.attachment_count = attachment_count
        row.processing_error = processing_error
        row.candidate_id = candidate_id
        row.job_id = job_id
        row.company_id = job.company_id if job else row.company_id
        row.outreach_event_id = outreach_event_id
        row.processed_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return row


class OtpRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, email: str, otp_hash: str, expires_at: datetime) -> OtpEntity:
        row = OtpEntity(
            id=str(uuid4()),
            email=email.lower().strip(),
            otp_hash=otp_hash,
            expires_at=expires_at,
            used=False,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def consume_valid(self, *, email: str, otp_hash: str, now: datetime) -> OtpEntity | None:
        stmt = (
            update(OtpEntity)
            .where(
                OtpEntity.email == email.lower().strip(),
                OtpEntity.otp_hash == otp_hash,
                OtpEntity.used == False,  # noqa: E712
                OtpEntity.expires_at > now,
            )
            .values(used=True)
            .returning(OtpEntity)
        )
        return self.db.scalar(stmt)


class ATSExportRetryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        candidate_ids: list[str],
        provider: str,
        next_retry_at: datetime,
    ) -> ATSExportRetryEntity:
        row = ATSExportRetryEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_ids=candidate_ids,
            provider=(provider or "mock").strip().lower() or "mock",
            next_retry_at=next_retry_at,
            attempt_count=0,
            status="pending",
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_due(self, *, now: datetime, max_attempts: int) -> list[ATSExportRetryEntity]:
        rows = self.db.scalars(
            select(ATSExportRetryEntity).where(
                ATSExportRetryEntity.status == "pending",
                ATSExportRetryEntity.next_retry_at <= now,
                ATSExportRetryEntity.attempt_count < max_attempts,
            )
        ).all()
        return list(rows)

    def mark_exhausted(self, row: ATSExportRetryEntity, error: str) -> None:
        row.status = "exhausted"
        row.last_error = error
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()

    def increment_attempt(
        self,
        row: ATSExportRetryEntity,
        *,
        error: str,
        next_retry_at: datetime,
    ) -> None:
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.last_error = error
        row.next_retry_at = next_retry_at
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
