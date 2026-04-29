from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.utils.exceptions import APIError
from app.models.entities import (
    ATSExportEntity,
    ATSExportRetryEntity,
    CandidateFeedbackEntity,
    CandidateProfileEntity,
    CompanyEntity,
    InterviewEntity,
    InterviewSessionEntity,
    JobEntity,
    OtpEntity,
    OutreachEventEntity,
    ScoringProfileEntity,
    UserEntity,
)
from app.core.config import ENABLE_FAKE_EMAILS, RLHF_BASE_FEEDBACK_BIAS, RLHF_MIN_FEEDBACK_BIAS, RLHF_SMOOTHING_ALPHA

logger = logging.getLogger(__name__)


def ensure_candidate_profile(db: Session, job_id: str, candidate_id: str) -> CandidateProfileEntity:
    return CandidateProfileRepository(db).ensure_candidate_profile(job_id=job_id, candidate_id=candidate_id)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _candidate_email_value(value: object) -> str:
    email = _normalize_text(value).lower()
    if "@" not in email:
        return ""
    return email


def _build_dev_email(*, name: str, candidate_id: str) -> str:
    safe_name = re.sub(r"[^a-z0-9]+", "", _normalize_text(name).lower()) or "candidate"
    safe_id = re.sub(r"[^a-z0-9]+", "", _normalize_text(candidate_id).lower())[:6] or "000000"
    return f"{safe_name}_{safe_id}@test.local"


def _ensure_candidate_profile_email(row: CandidateProfileEntity) -> bool:
    if not ENABLE_FAKE_EMAILS:
        return False

    raw_data = dict(row.raw_data or {})
    existing = (
        _candidate_email_value(raw_data.get("work_email"))
        or _candidate_email_value(raw_data.get("email"))
        or _candidate_email_value(raw_data.get("personal_email"))
    )
    if existing:
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

    def create(self, email: str) -> UserEntity:
        entity = UserEntity(id=str(uuid4()), email=email.lower().strip())
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
        title: str,
        description: str,
        location: str,
        compensation: str,
        work_authorization: str,
        vetting_mode: str = "volume",
        auto_export_to_ats: bool = False,
        ats_job_id: str | None = None,
        responsibilities: list[str] | None = None,
        skills_required: list[str] | None = None,
        experience_level: str = "",
        structured_data: dict | None = None,
    ) -> JobEntity:
        entity = JobEntity(
            id=str(uuid4()),
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
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def get(self, job_id: str) -> JobEntity | None:
        return self.db.scalar(select(JobEntity).where(JobEntity.id == job_id))

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
        self.db.flush()
        return job

    def get_candidate_sourcing_state(self, job_id: str) -> tuple[str, datetime | None] | None:
        job = self.get(job_id)
        if not job:
            return None
        return (job.job_status or "active").strip().lower(), job.last_candidate_attempt_at

    def list_recent(self, limit: int = 50) -> list[JobEntity]:
        rows = self.db.scalars(select(JobEntity).order_by(JobEntity.created_at.desc()).limit(limit)).all()
        return list(rows)

    def update_description(self, job_id: str, description: str) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None
        job.description = description
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
        if structured_data is not None:
            job.structured_data = structured_data

        self.db.flush()
        return job

    def set_vetting_mode(self, *, job_id: str, vetting_mode: str) -> JobEntity | None:
        job = self.get(job_id)
        if not job:
            return None
        normalized = (vetting_mode or "volume").strip().lower()
        job.vetting_mode = normalized if normalized in {"volume", "elite"} else "volume"
        self.db.flush()
        return job


class InterviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_job_and_candidate(self, job_id: str, candidate_id: str) -> InterviewEntity | None:
        return self.db.scalar(
            select(InterviewEntity).where(
                InterviewEntity.job_id == job_id,
                InterviewEntity.candidate_id == candidate_id,
            )
        )

    def upsert_status(self, *, job_id: str, candidate_id: str, status: str, create_default: str = "shortlisted") -> InterviewEntity:
        candidate_id = (candidate_id or "").strip()
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
        if not row:
            row = InterviewEntity(
                id=str(uuid4()),
                job_id=job_id,
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
        rows = self.db.scalars(select(InterviewEntity).where(InterviewEntity.job_id == job_id)).all()
        return list(rows)


class InterviewSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        candidate_id: str,
        email: str,
        token: str,
        expires_at: datetime,
        status: str = "pending",
    ) -> InterviewSessionEntity:
        existing = self.get_by_token(token)
        if existing:
            existing.job_id = job_id
            existing.candidate_id = candidate_id
            existing.email = email
            existing.expires_at = expires_at
            existing.status = status
            existing.booked_at = None
            self.db.flush()
            return existing

        row = InterviewSessionEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_id=candidate_id,
            email=email,
            token=token,
            status=status,
            expires_at=expires_at,
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
        if row and _ensure_candidate_profile_email(row):
            self.db.flush()
        return row

    @staticmethod
    def _profile_email_values(row: CandidateProfileEntity) -> list[str]:
        raw_data = row.raw_data if isinstance(row.raw_data, dict) else {}
        values = [
            _candidate_email_value(raw_data.get("work_email")),
            _candidate_email_value(raw_data.get("email")),
            _candidate_email_value(raw_data.get("personal_email")),
        ]
        return [value for value in values if value]

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

        row = CandidateProfileEntity(
            id=str(uuid4()),
            job_id=job_id,
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
        if not row:
            row = CandidateProfileEntity(
                id=str(uuid4()),
                job_id=job_id,
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
        row.fit_score = fit_score
        row.decision = decision
        row.strategy = strategy
        row.last_scored_at = now
        row.last_refreshed_at = now
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

    def latest_by_candidate_ids(self, *, job_id: str, candidate_ids: list[str]) -> dict[str, CandidateProfileEntity]:
        unique_ids = list(dict.fromkeys(candidate_ids))
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

    def upsert(self, *, job_id: str, candidate_id: str, feedback: str) -> CandidateFeedbackEntity:
        feedback = feedback.strip().lower()
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = CandidateFeedbackEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                feedback=feedback,
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
        self.db.add(row)
        self.db.flush()
        return row

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

    def get(self, *, job_id: str, candidate_id: str) -> OutreachEventEntity | None:
        return self.db.scalar(
            select(OutreachEventEntity).where(
                OutreachEventEntity.job_id == job_id,
                OutreachEventEntity.candidate_id == candidate_id,
            )
        )

    def get_by_provider_message_id(self, provider_message_id: str) -> OutreachEventEntity | None:
        if not provider_message_id:
            return None
        return self.db.scalar(
            select(OutreachEventEntity).where(OutreachEventEntity.provider_message_id == provider_message_id)
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
        ensure_candidate_profile(self.db, job_id, candidate_id)

        row = self.get(job_id=job_id, candidate_id=candidate_id)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                job_id=job_id,
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
                provider=provider or row.provider,
                to_email=to_email or row.to_email,
                subject=subject or row.subject,
                body=body or row.body,
                status="sending",
                last_error="",
                attempt_count=func.coalesce(OutreachEventEntity.attempt_count, 0) + 1,
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
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                attempt_count=0,
                follow_up_count=0,
            )
            self.db.add(row)
            self.db.flush()

        row.provider = provider
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
            row.last_sent_at = sent_at
            row.last_contacted_at = sent_at
        row.next_follow_up_at = next_follow_up_at
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
        ensure_candidate_profile(self.db, job_id, candidate_id)
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.now(timezone.utc)
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                attempt_count=0,
                follow_up_count=0,
            )
            self.db.add(row)
            self.db.flush()

        row.provider = provider
        row.message_text = message_text.strip()
        row.resume_url = resume_url.strip()
        row.status = status
        if provider_message_id and not (row.provider_message_id or "").strip():
            row.provider_message_id = provider_message_id
        row.last_error = last_error
        row.last_contacted_at = received_at or now
        row.responded_at = received_at or now
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[OutreachEventEntity]:
        rows = self.db.scalars(select(OutreachEventEntity).where(OutreachEventEntity.job_id == job_id)).all()
        return list(rows)

    def list_due_follow_ups(self, *, now: datetime, max_follow_up_count: int) -> list[OutreachEventEntity]:
        """Return outreach events that are due for a follow-up and haven't exceeded max attempts."""
        rows = self.db.scalars(
            select(OutreachEventEntity).where(
                OutreachEventEntity.status == "sent",
                OutreachEventEntity.next_follow_up_at <= now,
                OutreachEventEntity.follow_up_count < max_follow_up_count,
                OutreachEventEntity.to_email != "",
            )
        ).all()
        return list(rows)

    def list_replied(self, *, job_id: str | None = None) -> list[OutreachEventEntity]:
        stmt = select(OutreachEventEntity).where(OutreachEventEntity.status == "replied")
        if job_id:
            stmt = stmt.where(OutreachEventEntity.job_id == job_id)
        rows = self.db.scalars(stmt).all()
        return list(rows)

    def list_due_follow_ups_locked(self, *, now: datetime, max_follow_up_count: int) -> list[OutreachEventEntity]:
        stmt = (
            select(OutreachEventEntity)
            .where(
                OutreachEventEntity.status == "sent",
                OutreachEventEntity.next_follow_up_at <= now,
                OutreachEventEntity.follow_up_count < max_follow_up_count,
                OutreachEventEntity.to_email != "",
            )
            .with_for_update(skip_locked=True)
        )
        rows = self.db.scalars(stmt).all()
        return list(rows)


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
