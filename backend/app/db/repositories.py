from __future__ import annotations

import math
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    ATSExportEntity,
    CandidateFeedbackEntity,
    CandidateProfileEntity,
    CompanyEntity,
    InterviewEntity,
    JobEntity,
    OutreachEventEntity,
    ScoringProfileEntity,
    UserEntity,
)
from app.core.config import RLHF_BASE_FEEDBACK_BIAS, RLHF_SMOOTHING_ALPHA


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
            name=name.strip(),
            website=website.strip(),
            description=description.strip(),
            industry=industry.strip(),
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def update_profile(
        self,
        *,
        company_id: str,
        name: str | None = None,
        description: str | None = None,
        industry: str | None = None,
    ) -> CompanyEntity | None:
        company = self.db.scalar(select(CompanyEntity).where(CompanyEntity.id == company_id))
        if not company:
            return None

        if name is not None:
            company.name = name.strip()
        if description is not None:
            company.description = description.strip()
        if industry is not None:
            company.industry = industry.strip()

        self.db.flush()
        return company


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
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def get(self, job_id: str) -> JobEntity | None:
        return self.db.scalar(select(JobEntity).where(JobEntity.id == job_id))

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
        if structured_data is not None:
            job.structured_data = structured_data

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
        row = self.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
        if not row:
            row = InterviewEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                status=create_default,
            )
            self.db.add(row)
            self.db.flush()

        row.status = status
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[InterviewEntity]:
        rows = self.db.scalars(select(InterviewEntity).where(InterviewEntity.job_id == job_id)).all()
        return list(rows)


class CandidateProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, *, job_id: str, candidate_id: str) -> CandidateProfileEntity | None:
        return self.db.scalar(
            select(CandidateProfileEntity).where(
                CandidateProfileEntity.job_id == job_id,
                CandidateProfileEntity.candidate_id == candidate_id,
            )
        )

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
        now = datetime.utcnow()
        if not row:
            row = CandidateProfileEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
            )
            self.db.add(row)
            self.db.flush()

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
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[CandidateProfileEntity]:
        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .where(CandidateProfileEntity.job_id == job_id)
            .order_by(CandidateProfileEntity.fit_score.desc())
        ).all()
        return list(rows)

    def latest_by_candidate_ids(self, candidate_ids: list[str]) -> dict[str, CandidateProfileEntity]:
        unique_ids = list(dict.fromkeys(candidate_ids))
        if not unique_ids:
            return {}

        rows = self.db.scalars(
            select(CandidateProfileEntity)
            .where(CandidateProfileEntity.candidate_id.in_(unique_ids))
            .order_by(CandidateProfileEntity.last_scored_at.desc())
        ).all()

        latest: dict[str, CandidateProfileEntity] = {}
        for row in rows:
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
        now = datetime.utcnow()
        if not row:
            row = CandidateFeedbackEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                feedback=feedback,
                created_at=now,
            )
            self.db.add(row)
            self.db.flush()

        row.feedback = feedback
        row.accepted = feedback == "accept"
        row.rejected = feedback == "reject"
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[CandidateFeedbackEntity]:
        rows = self.db.scalars(select(CandidateFeedbackEntity).where(CandidateFeedbackEntity.job_id == job_id)).all()
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
        row.feedback_bias = RLHF_BASE_FEEDBACK_BIAS / max(1.0, math.sqrt(max(1, feedback_count)))

        row.updated_at = datetime.utcnow()
        self.db.flush()
        return row


class ATSExportRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        candidate_ids: list[str],
        provider: str,
        status: str,
        external_reference: str,
        response_payload: dict,
    ) -> ATSExportEntity:
        row = ATSExportEntity(
            id=str(uuid4()),
            job_id=job_id,
            candidate_ids=candidate_ids,
            provider=provider,
            status=status,
            external_reference=external_reference,
            response_payload=response_payload,
        )
        self.db.add(row)
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
    ) -> OutreachEventEntity:
        row = self.get(job_id=job_id, candidate_id=candidate_id)
        now = datetime.utcnow()
        if not row:
            row = OutreachEventEntity(
                id=str(uuid4()),
                job_id=job_id,
                candidate_id=candidate_id,
                attempt_count=0,
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
        row.last_sent_at = sent_at
        row.next_follow_up_at = next_follow_up_at
        row.updated_at = now
        self.db.flush()
        return row

    def list_for_job(self, job_id: str) -> list[OutreachEventEntity]:
        rows = self.db.scalars(select(OutreachEventEntity).where(OutreachEventEntity.job_id == job_id)).all()
        return list(rows)
