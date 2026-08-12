"""
Scheduler startup wiring tests.

Proves:
1. start_scheduler is importable from refresh_scheduler
2. start_scheduler is imported in main (wiring present)
3. start_scheduler() starts exactly one daemon thread named pontis-scheduler
4. Calling start_scheduler() a second time does NOT create a second thread
5. stop_scheduler() signals the thread to stop; thread exits within timeout
6. REFRESH_CRON_ENABLED=False prevents the thread from starting
7. _run_automation_cycle in the scheduler loop calls run_automation_cycle from automation_service
8. A due interview_execution automation job is discovered and executed by run_automation_cycle
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import (
    Base,
    CandidateProfileEntity,
    CandidateRequestEntity,
    CompanyEntity,
    InterviewSessionEntity,
    JobEntity,
    NotificationWorkflowTokenEntity,
    UserEntity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _reset_scheduler_module():
    """Reset module-level scheduler state between tests."""
    import app.services.refresh_scheduler as sched
    sched._scheduler_stop.set()
    if sched._scheduler_thread and sched._scheduler_thread.is_alive():
        sched._scheduler_thread.join(timeout=2)
    sched._scheduler_thread = None
    sched._scheduler_stop.clear()


# ---------------------------------------------------------------------------
# 1. start_scheduler is importable
# ---------------------------------------------------------------------------

class TestImportable:
    def test_start_scheduler_importable(self):
        from app.services.refresh_scheduler import start_scheduler
        assert callable(start_scheduler)

    def test_stop_scheduler_importable(self):
        from app.services.refresh_scheduler import stop_scheduler
        assert callable(stop_scheduler)

    def test_scheduler_status_importable(self):
        from app.services.refresh_scheduler import scheduler_status
        assert callable(scheduler_status)


# ---------------------------------------------------------------------------
# 2. main.py imports start_scheduler (wiring present)
# ---------------------------------------------------------------------------

class TestMainWiring:
    def test_start_scheduler_in_main_imports(self):
        import ast, pathlib
        src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "start_scheduler" in imported_names, (
            "start_scheduler must be imported in main.py — it was missing before this fix"
        )

    def test_start_scheduler_called_in_on_startup(self):
        import pathlib
        src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
        assert "start_scheduler()" in src, (
            "start_scheduler() must be called inside on_startup in main.py"
        )

    def test_old_dead_log_removed(self):
        import pathlib
        src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
        assert "startup_background_services_not_started" not in src, (
            "The placeholder log that replaced start_scheduler() must be removed"
        )


# ---------------------------------------------------------------------------
# 3. start_scheduler() starts exactly one daemon thread
# ---------------------------------------------------------------------------

class TestSchedulerStartsThread:
    def setup_method(self):
        _reset_scheduler_module()

    def teardown_method(self):
        _reset_scheduler_module()

    def test_start_scheduler_creates_daemon_thread(self):
        import app.services.refresh_scheduler as sched

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", return_value=None):
            sched.start_scheduler()
            assert sched._scheduler_thread is not None
            assert sched._scheduler_thread.daemon is True
            assert sched._scheduler_thread.name == "pontis-scheduler"

    def test_thread_is_alive_after_start(self):
        import app.services.refresh_scheduler as sched

        started = threading.Event()

        def _fake_loop():
            started.set()
            sched._scheduler_stop.wait()

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            sched.start_scheduler()
            assert started.wait(timeout=2), "Scheduler thread did not start within 2s"
            assert sched._scheduler_thread.is_alive()


# ---------------------------------------------------------------------------
# 4. No duplicate scheduler on second call
# ---------------------------------------------------------------------------

class TestNoDuplicateScheduler:
    def setup_method(self):
        _reset_scheduler_module()

    def teardown_method(self):
        _reset_scheduler_module()

    def test_second_start_call_is_noop(self):
        import app.services.refresh_scheduler as sched

        started = threading.Event()

        def _fake_loop():
            started.set()
            sched._scheduler_stop.wait()

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            sched.start_scheduler()
            first_thread = sched._scheduler_thread
            started.wait(timeout=2)

            sched.start_scheduler()  # second call
            assert sched._scheduler_thread is first_thread, (
                "Second call to start_scheduler() must not replace the running thread"
            )

    def test_thread_count_stays_one(self):
        import app.services.refresh_scheduler as sched

        started = threading.Event()

        def _fake_loop():
            started.set()
            sched._scheduler_stop.wait()

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            before = {t.name for t in threading.enumerate()}
            sched.start_scheduler()
            started.wait(timeout=2)
            sched.start_scheduler()
            sched.start_scheduler()
            after = [t for t in threading.enumerate() if t.name == "pontis-scheduler"]
            assert len(after) == 1


# ---------------------------------------------------------------------------
# 5. stop_scheduler() signals thread to exit
# ---------------------------------------------------------------------------

class TestShutdown:
    def setup_method(self):
        _reset_scheduler_module()

    def teardown_method(self):
        _reset_scheduler_module()

    def test_stop_scheduler_sets_stop_event(self):
        import app.services.refresh_scheduler as sched

        def _fake_loop():
            sched._scheduler_stop.wait()

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            sched.start_scheduler()
            time.sleep(0.05)
            sched.stop_scheduler()
            assert sched._scheduler_stop.is_set()

    def test_thread_exits_after_stop(self):
        import app.services.refresh_scheduler as sched

        def _fake_loop():
            sched._scheduler_stop.wait(timeout=5)

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            sched.start_scheduler()
            time.sleep(0.05)
            sched.stop_scheduler()
            sched._scheduler_thread.join(timeout=3)
            assert not sched._scheduler_thread.is_alive(), (
                "Scheduler thread must exit after stop_scheduler() is called"
            )

    def test_scheduler_status_reports_not_running_after_stop(self):
        import app.services.refresh_scheduler as sched

        def _fake_loop():
            sched._scheduler_stop.wait(timeout=5)

        with patch.object(sched, "REFRESH_CRON_ENABLED", True), \
             patch.object(sched, "_run_loop", side_effect=_fake_loop):
            sched.start_scheduler()
            time.sleep(0.05)
            sched.stop_scheduler()
            sched._scheduler_thread.join(timeout=3)
            status = sched.scheduler_status()
            assert status["running"] is False


# ---------------------------------------------------------------------------
# 6. REFRESH_CRON_ENABLED=False prevents start
# ---------------------------------------------------------------------------

class TestDisabledByConfig:
    def setup_method(self):
        _reset_scheduler_module()

    def teardown_method(self):
        _reset_scheduler_module()

    def test_disabled_config_prevents_thread_start(self):
        import app.services.refresh_scheduler as sched

        with patch.object(sched, "REFRESH_CRON_ENABLED", False):
            sched.start_scheduler()
            assert sched._scheduler_thread is None

    def test_disabled_config_status_reports_not_running(self):
        import app.services.refresh_scheduler as sched

        with patch.object(sched, "REFRESH_CRON_ENABLED", False):
            sched.start_scheduler()
            status = sched.scheduler_status()
            assert status["running"] is False


# ---------------------------------------------------------------------------
# 7. _run_automation_cycle calls run_automation_cycle from automation_service
# ---------------------------------------------------------------------------

class TestAutomationCycleWiring:
    def test_run_automation_cycle_is_called(self):
        """
        _run_automation_cycle() in the scheduler must delegate to
        automation_service.run_automation_cycle — not a stub.
        """
        import app.services.refresh_scheduler as sched

        calls = []

        def fake_run_automation_cycle(*, db):
            calls.append(True)
            return {"seeded": 0, "executed": 0, "failed": 0}

        fake_db = MagicMock()
        fake_session_ctx = MagicMock()
        fake_session_ctx.__enter__ = MagicMock(return_value=fake_db)
        fake_session_ctx.__exit__ = MagicMock(return_value=False)

        lock_ctx = MagicMock()
        lock_ctx.__enter__ = MagicMock(return_value=True)
        lock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("app.services.refresh_scheduler.SessionLocal", return_value=fake_session_ctx), \
             patch("app.services.automation_service.run_automation_cycle", side_effect=fake_run_automation_cycle), \
             patch("app.services.redis_service.distributed_lock", return_value=lock_ctx):
            sched._run_automation_cycle()

        assert calls, "_run_automation_cycle() must call run_automation_cycle()"


# ---------------------------------------------------------------------------
# 8. Due interview_execution job is discovered and executed by run_automation_cycle
# ---------------------------------------------------------------------------

class TestInterviewExecutionDiscovery:
    @pytest.fixture
    def db(self):
        session = _make_db()
        yield session
        session.close()

    def _make_scheduled_session(self, db):
        agency = CompanyEntity(id=str(uuid4()), name="Agency", slug="agency")
        db.add(agency)
        db.flush()

        recruiter = UserEntity(
            id=str(uuid4()), email="r@test.com", role="recruiter", agency_id=agency.id
        )
        db.add(recruiter)
        db.flush()

        job = JobEntity(
            id=str(uuid4()), title="Eng", agency_id=agency.id,
            created_by=recruiter.id, source_app="ui", job_status="active",
            vetting_mode="volume", created_by_source="PONTIS", updated_by_source="PONTIS",
        )
        db.add(job)
        db.flush()

        cid = f"cand-{uuid4().hex[:8]}"
        profile = CandidateProfileEntity(
            id=str(uuid4()), candidate_id=cid, job_id=job.id, agency_id=agency.id,
            name="Test", email="c@test.com",
            raw_data={"email": "c@test.com"},
            created_by_source="PONTIS", updated_by_source="PONTIS",
        )
        db.add(profile)
        db.flush()

        req = CandidateRequestEntity(
            id=str(uuid4()), candidate_id=cid, job_id=job.id,
            agency_id=agency.id, status="ACCEPTED", created_by=recruiter.id,
        )
        db.add(req)
        db.commit()
        return job, profile

    def test_due_interview_execution_job_is_executed(self, db, monkeypatch):
        """
        A booking creates an interview_execution automation job scheduled at
        interview time.  When that time is in the past, run_automation_cycle()
        must pick it up and execute it.
        """
        monkeypatch.setattr(
            "app.services.first_round_interview_service._send_booking_email",
            lambda **kw: None,
        )
        from app.db.repositories import AutomationJobRepository
        from app.services.automation_service import run_automation_cycle
        from app.services.first_round_interview_service import request_first_round_interview
        from app.services.interview_session_service import book_interview_session

        job, profile = self._make_scheduled_session(db)

        # Slot in the past so the automation job is immediately due
        past_slot = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        result = request_first_round_interview(
            db,
            candidate_id=profile.candidate_id,
            job_id=job.id,
            recruiter_id=db.query(UserEntity).first().id,
            available_slots=[past_slot],
        )
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=past_slot)

        # Confirm the automation job was created
        auto_job = AutomationJobRepository(db).get_by_key(f"interview-execution:{token}")
        assert auto_job is not None
        assert auto_job.automation_type == "interview_execution"

        # Patch _trigger_interview_execution so we don't need INTERVIEW_APP_URL
        trigger_calls = []

        def fake_trigger(*, db, session, workflow_token):
            trigger_calls.append(session.session_token)
            return {"status": "ready", "interviewUrl": "https://interview.pontis.one/interview?token=x"}

        monkeypatch.setattr(
            "app.services.automation_service._trigger_interview_execution",
            fake_trigger,
        )

        summary = run_automation_cycle(db=db, scan_limit=10)

        assert summary["executed"] >= 1, (
            f"run_automation_cycle must execute the due interview_execution job; got {summary}"
        )
        assert trigger_calls, "interview_execution handler must have been reached"

    def test_future_interview_execution_job_is_not_yet_executed(self, db, monkeypatch):
        """
        A job scheduled far in the future must NOT be picked up by the current cycle.
        """
        monkeypatch.setattr(
            "app.services.first_round_interview_service._send_booking_email",
            lambda **kw: None,
        )
        from app.db.repositories import AutomationJobRepository
        from app.services.automation_service import run_automation_cycle
        from app.services.first_round_interview_service import request_first_round_interview
        from app.services.interview_session_service import book_interview_session

        job, profile = self._make_scheduled_session(db)

        future_slot = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        result = request_first_round_interview(
            db,
            candidate_id=profile.candidate_id,
            job_id=job.id,
            recruiter_id=db.query(UserEntity).first().id,
            available_slots=[future_slot],
        )
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=future_slot)

        trigger_calls = []

        def fake_trigger(*, db, session, workflow_token):
            trigger_calls.append(session.session_token)
            return {"status": "ready", "interviewUrl": "https://interview.pontis.one/interview?token=x"}

        monkeypatch.setattr(
            "app.services.automation_service._trigger_interview_execution",
            fake_trigger,
        )

        run_automation_cycle(db=db, scan_limit=10)

        assert not trigger_calls, (
            "A future interview_execution job must not be executed before its scheduled time"
        )
