-- adam_schema.sql
-- Source of truth: backend/app/models/entities.py and runtime-altering Alembic migrations.
-- Scope: Adam runtime tables only.

CREATE TABLE users (
	id UUID NOT NULL,
	email VARCHAR(320) NOT NULL,
	role VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE companies (
	id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	website VARCHAR(500) NOT NULL,
	description TEXT NOT NULL,
	industry VARCHAR(255) NOT NULL,
	ats_provider VARCHAR(64) NOT NULL,
	ats_connected BOOLEAN NOT NULL,
	user_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_companies_user_name UNIQUE (user_id, name),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_companies_user_id ON companies (user_id);

CREATE TABLE otps (
	id UUID NOT NULL,
	email VARCHAR(320) NOT NULL,
	otp_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	used BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX ix_otps_email ON otps (email);
CREATE INDEX ix_otps_email_expires ON otps (email, expires_at) WHERE used = false;

CREATE TABLE jobs (
	id UUID NOT NULL,
	source_app VARCHAR(32) NOT NULL,
	job_status VARCHAR(32) NOT NULL,
	vetting_mode VARCHAR(16) NOT NULL,
	title VARCHAR(255) NOT NULL,
	description TEXT NOT NULL,
	responsibilities JSON NOT NULL,
	skills_required JSON NOT NULL,
	experience_level VARCHAR(255) NOT NULL,
	location VARCHAR(255) NOT NULL,
	compensation VARCHAR(255) NOT NULL,
	structured_data JSON NOT NULL,
	work_authorization VARCHAR(64) NOT NULL,
	ats_job_id VARCHAR(128),
	auto_export_to_ats BOOLEAN NOT NULL,
	company_id UUID NOT NULL,
	created_by UUID NOT NULL,
	remote_policy VARCHAR(64) NOT NULL,
	experience_required VARCHAR(255) NOT NULL,
	last_candidate_attempt_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(created_by) REFERENCES users (id)
);
CREATE INDEX ix_jobs_created_by ON jobs (created_by);
CREATE INDEX ix_jobs_company_id ON jobs (company_id);
CREATE INDEX ix_jobs_status_created ON jobs (job_status, created_at DESC);

CREATE TABLE job_intakes (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	transcript TEXT NOT NULL,
	structured_data_json JSON NOT NULL,
	intake_status VARCHAR(32) NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_job_intakes_job UNIQUE (job_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_job_intakes_job_id ON job_intakes (job_id);
CREATE INDEX ix_job_intakes_company_id ON job_intakes (company_id);

CREATE TABLE candidate_profiles (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	name VARCHAR(255) NOT NULL,
	role VARCHAR(255) NOT NULL,
	company VARCHAR(255) NOT NULL,
	summary TEXT NOT NULL,
	skills JSON NOT NULL,
	raw_data JSON NOT NULL,
	candidate_status VARCHAR(64) NOT NULL,
	resume_received_at TIMESTAMP WITH TIME ZONE,
	total_experience_years FLOAT NOT NULL,
	current_title VARCHAR(255) NOT NULL,
	current_company VARCHAR(255) NOT NULL,
	phone VARCHAR(64) NOT NULL,
	linkedin_url VARCHAR(500) NOT NULL,
	github_url VARCHAR(500) NOT NULL,
	parsed_resume_json JSON NOT NULL,
	parsed_resume_text TEXT NOT NULL,
	fit_score FLOAT NOT NULL,
	decision VARCHAR(64) NOT NULL,
	strategy VARCHAR(32) NOT NULL,
	last_scored_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_refreshed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ats_status VARCHAR(64) NOT NULL,
	ats_status_source VARCHAR(32) NOT NULL,
	ats_status_reason TEXT NOT NULL,
	ats_status_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ats_metadata JSON NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_candidate_profiles_job_candidate UNIQUE (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_candidate_profiles_candidate_id ON candidate_profiles (candidate_id);
CREATE INDEX ix_candidate_profiles_job_id ON candidate_profiles (job_id);
CREATE INDEX ix_candidate_profiles_company_id ON candidate_profiles (company_id);
CREATE INDEX ix_candidate_profiles_job_fit ON candidate_profiles (job_id, fit_score DESC);
CREATE INDEX ix_candidate_profiles_last_refreshed ON candidate_profiles (last_refreshed_at ASC);

CREATE TABLE candidate_feedback (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	feedback VARCHAR(16) NOT NULL,
	accepted BOOLEAN NOT NULL,
	rejected BOOLEAN NOT NULL,
	recruiter_id UUID,
	session_id UUID,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_candidate_feedback_job_candidate UNIQUE (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(recruiter_id) REFERENCES users (id),
	FOREIGN KEY(session_id) REFERENCES candidate_selection_sessions (id)
);
CREATE INDEX ix_candidate_feedback_recruiter_id ON candidate_feedback (recruiter_id);
CREATE INDEX ix_candidate_feedback_candidate_id ON candidate_feedback (candidate_id);
CREATE INDEX ix_candidate_feedback_session_id ON candidate_feedback (session_id);
CREATE INDEX ix_candidate_feedback_job_id ON candidate_feedback (job_id);
CREATE INDEX ix_candidate_feedback_recruiter ON candidate_feedback (recruiter_id) WHERE recruiter_id IS NOT NULL;
CREATE INDEX ix_candidate_feedback_updated_at ON candidate_feedback (updated_at DESC);

CREATE TABLE candidate_selection_sessions (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	status VARCHAR(32) NOT NULL,
	current_batch_index INTEGER NOT NULL,
	batch_size INTEGER NOT NULL,
	total_batches INTEGER NOT NULL,
	candidate_pool_snapshot JSON NOT NULL,
	batch_plan JSON NOT NULL,
	selected_candidate_ids JSON NOT NULL,
	rejected_candidate_ids JSON NOT NULL,
	batch_history JSON NOT NULL,
	selection_analysis JSON NOT NULL,
	final_candidate_snapshot JSON NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_candidate_selection_sessions_job_id ON candidate_selection_sessions (job_id);

CREATE TABLE candidate_lifecycle_events (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	from_status VARCHAR(64) NOT NULL,
	to_status VARCHAR(64) NOT NULL,
	source VARCHAR(32) NOT NULL,
	actor_id UUID,
	transition_key VARCHAR(255) NOT NULL,
	event_metadata JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_candidate_lifecycle_events_transition UNIQUE (job_id, candidate_id, transition_key),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
CREATE INDEX ix_candidate_lifecycle_events_candidate_id ON candidate_lifecycle_events (candidate_id);
CREATE INDEX ix_candidate_lifecycle_events_actor_id ON candidate_lifecycle_events (actor_id);
CREATE INDEX ix_candidate_lifecycle_events_company_id ON candidate_lifecycle_events (company_id);
CREATE INDEX ix_candidate_lifecycle_events_job_id ON candidate_lifecycle_events (job_id);

CREATE TABLE scoring_profiles (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	weight_pdl FLOAT NOT NULL,
	weight_semantic FLOAT NOT NULL,
	weight_skill FLOAT NOT NULL,
	weight_recency FLOAT NOT NULL,
	feedback_bias FLOAT NOT NULL,
	elite_reasoning_bonus FLOAT NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_scoring_profiles_job UNIQUE (job_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_scoring_profiles_job_id ON scoring_profiles (job_id);

CREATE TABLE ranking_runs (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	recruiter_id UUID,
	run_type VARCHAR(32) NOT NULL,
	avg_existing_score FLOAT NOT NULL,
	avg_final_score FLOAT NOT NULL,
	avg_recruiter_score FLOAT NOT NULL,
	percent_recruiter_capped FLOAT NOT NULL,
	candidate_count INTEGER NOT NULL,
	drift_delta FLOAT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(recruiter_id) REFERENCES users (id)
);
CREATE INDEX ix_ranking_runs_recruiter_id ON ranking_runs (recruiter_id);
CREATE INDEX ix_ranking_runs_job_id ON ranking_runs (job_id);
CREATE INDEX ix_ranking_runs_created_at ON ranking_runs (created_at DESC);

CREATE TABLE ranking_explanations (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	existing_score FLOAT NOT NULL,
	recruiter_score FLOAT NOT NULL,
	session_signal FLOAT NOT NULL,
	final_score FLOAT NOT NULL,
	recruiter_capped BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ranking_explanations_job_candidate UNIQUE (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_ranking_explanations_company_id ON ranking_explanations (company_id);
CREATE INDEX ix_ranking_explanations_job_id ON ranking_explanations (job_id);
CREATE INDEX ix_ranking_explanations_candidate_id ON ranking_explanations (candidate_id);

CREATE TABLE recruiter_experience_preferences (
	id UUID NOT NULL,
	recruiter_id UUID NOT NULL,
	experience_bucket VARCHAR(16) NOT NULL,
	weight FLOAT NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_recruiter_experience_preferences_recruiter_bucket UNIQUE (recruiter_id, experience_bucket),
	FOREIGN KEY(recruiter_id) REFERENCES users (id)
);
CREATE INDEX ix_recruiter_experience_preferences_recruiter_id ON recruiter_experience_preferences (recruiter_id);
CREATE INDEX ix_recruiter_experience_preferences_experience_bucket ON recruiter_experience_preferences (experience_bucket);

CREATE TABLE recruiter_role_preferences (
	id UUID NOT NULL,
	recruiter_id UUID NOT NULL,
	role VARCHAR(255) NOT NULL,
	weight FLOAT NOT NULL,
	positive_count INTEGER NOT NULL,
	negative_count INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_recruiter_role_preferences_recruiter_role UNIQUE (recruiter_id, role),
	FOREIGN KEY(recruiter_id) REFERENCES users (id)
);
CREATE INDEX ix_recruiter_role_preferences_recruiter_id ON recruiter_role_preferences (recruiter_id);
CREATE INDEX ix_recruiter_role_preferences_role ON recruiter_role_preferences (role);

CREATE TABLE recruiter_skill_preferences (
	id UUID NOT NULL,
	recruiter_id UUID NOT NULL,
	skill VARCHAR(255) NOT NULL,
	weight FLOAT NOT NULL,
	positive_count INTEGER NOT NULL,
	negative_count INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_recruiter_skill_preferences_recruiter_skill UNIQUE (recruiter_id, skill),
	FOREIGN KEY(recruiter_id) REFERENCES users (id)
);
CREATE INDEX ix_recruiter_skill_preferences_skill ON recruiter_skill_preferences (skill);
CREATE INDEX ix_recruiter_skill_preferences_recruiter_id ON recruiter_skill_preferences (recruiter_id);

CREATE TABLE outreach_events (
	id UUID NOT NULL,
	source_app VARCHAR(32) NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	provider VARCHAR(64) NOT NULL,
	to_email VARCHAR(320) NOT NULL,
	subject VARCHAR(255) NOT NULL,
	body TEXT NOT NULL,
	status VARCHAR(64) NOT NULL,
	reply_state VARCHAR(64) NOT NULL,
	archive_reason VARCHAR(255) NOT NULL,
	attempt_count INTEGER NOT NULL,
	follow_up_count INTEGER NOT NULL,
	open_count INTEGER NOT NULL,
	reply_count INTEGER NOT NULL,
	provider_message_id VARCHAR(255),
	last_error TEXT NOT NULL,
	sent_at TIMESTAMP WITH TIME ZONE,
	last_sent_at TIMESTAMP WITH TIME ZONE,
	last_contacted_at TIMESTAMP WITH TIME ZONE,
	last_opened_at TIMESTAMP WITH TIME ZONE,
	last_replied_at TIMESTAMP WITH TIME ZONE,
	next_follow_up_at TIMESTAMP WITH TIME ZONE,
	message_text TEXT NOT NULL,
	resume_url VARCHAR(500) NOT NULL,
	reply_intent VARCHAR(64) NOT NULL,
	responded_at TIMESTAMP WITH TIME ZONE,
	engagement_score FLOAT NOT NULL,
	reply_likelihood_score FLOAT NOT NULL,
	responsiveness_score FLOAT NOT NULL,
	learning_applied BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id, candidate_id) REFERENCES candidate_profiles (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_outreach_events_job_id ON outreach_events (job_id);
CREATE INDEX ix_outreach_events_company_id ON outreach_events (company_id);
CREATE INDEX ix_outreach_events_candidate_id ON outreach_events (candidate_id);
CREATE INDEX ix_outreach_events_followup ON outreach_events (status, next_follow_up_at, follow_up_count) WHERE status = 'sent';
CREATE INDEX ix_outreach_events_learning ON outreach_events (status, learning_applied, responded_at) WHERE learning_applied = false;

CREATE TABLE inbound_email_replies (
	id UUID NOT NULL,
	svix_id VARCHAR(255) NOT NULL,
	event_type VARCHAR(64) NOT NULL,
	email_id VARCHAR(255) NOT NULL,
	provider_message_id VARCHAR(255) NOT NULL,
	candidate_id VARCHAR(128),
	job_id UUID,
	company_id UUID,
	outreach_event_id UUID,
	sender_email VARCHAR(320) NOT NULL,
	sender_name VARCHAR(255) NOT NULL,
	subject VARCHAR(255) NOT NULL,
	body_text TEXT NOT NULL,
	body_html TEXT NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE NOT NULL,
	webhook_created_at TIMESTAMP WITH TIME ZONE,
	processing_status VARCHAR(32) NOT NULL,
	match_status VARCHAR(32) NOT NULL,
	intent VARCHAR(64) NOT NULL,
	attachment_count INTEGER NOT NULL,
	raw_payload JSON NOT NULL,
	processing_error TEXT NOT NULL,
	processed_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inbound_email_replies_svix_id UNIQUE (svix_id),
	CONSTRAINT uq_inbound_email_replies_email_id UNIQUE (email_id),
	CONSTRAINT uq_inbound_email_replies_provider_message_id UNIQUE (provider_message_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(outreach_event_id) REFERENCES outreach_events (id)
);
CREATE INDEX ix_inbound_email_replies_candidate_id ON inbound_email_replies (candidate_id);
CREATE INDEX ix_inbound_email_replies_job_id ON inbound_email_replies (job_id);
CREATE INDEX ix_inbound_email_replies_svix_id ON inbound_email_replies (svix_id);
CREATE INDEX ix_inbound_email_replies_email_id ON inbound_email_replies (email_id);
CREATE INDEX ix_inbound_email_replies_outreach_event_id ON inbound_email_replies (outreach_event_id);
CREATE INDEX ix_inbound_email_replies_company_id ON inbound_email_replies (company_id);

CREATE TABLE inbound_email_attachments (
	id UUID NOT NULL,
	reply_id UUID NOT NULL,
	provider_attachment_id VARCHAR(255) NOT NULL,
	filename VARCHAR(512) NOT NULL,
	content_type VARCHAR(255) NOT NULL,
	size_bytes INTEGER NOT NULL,
	storage_path VARCHAR(1024) NOT NULL,
	public_url VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inbound_email_attachments_reply_provider_attachment UNIQUE (reply_id, provider_attachment_id),
	FOREIGN KEY(reply_id) REFERENCES inbound_email_replies (id)
);
CREATE INDEX ix_inbound_email_attachments_reply_id ON inbound_email_attachments (reply_id);

CREATE TABLE notification_events (
	id UUID NOT NULL,
	job_id UUID,
	company_id UUID,
	candidate_id VARCHAR(128),
	actor_id UUID,
	recipient_type VARCHAR(32) NOT NULL,
	recipient VARCHAR(255) NOT NULL,
	channel VARCHAR(32) NOT NULL,
	title VARCHAR(255) NOT NULL,
	body TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	notification_type VARCHAR(64) NOT NULL,
	notification_key VARCHAR(255) NOT NULL,
	delivery_reference VARCHAR(255) NOT NULL,
	notification_metadata JSON NOT NULL,
	delivered_at TIMESTAMP WITH TIME ZONE,
	failed_at TIMESTAMP WITH TIME ZONE,
	read_at TIMESTAMP WITH TIME ZONE,
	is_read BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_events_notification_key UNIQUE (notification_key),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
CREATE INDEX ix_notification_events_actor_id ON notification_events (actor_id);
CREATE INDEX ix_notification_events_company_id ON notification_events (company_id);
CREATE INDEX ix_notification_events_candidate_id ON notification_events (candidate_id);
CREATE INDEX ix_notification_events_job_id ON notification_events (job_id);

CREATE TABLE notification_workflow_tokens (
	id UUID NOT NULL,
	source_app VARCHAR(32) NOT NULL,
	job_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	token_type VARCHAR(64) NOT NULL,
	workflow_name VARCHAR(64) NOT NULL,
	token VARCHAR(255) NOT NULL,
	is_active BOOLEAN NOT NULL,
	status VARCHAR(32) NOT NULL,
	payload JSON NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	consumed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_workflow_tokens_token UNIQUE (token),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_notification_workflow_tokens_candidate_id ON notification_workflow_tokens (candidate_id);
CREATE INDEX ix_notification_workflow_tokens_job_id ON notification_workflow_tokens (job_id);
CREATE INDEX ix_notification_workflow_tokens_token ON notification_workflow_tokens (token);

CREATE TABLE interview_sessions (
	id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	outreach_event_id UUID,
	email VARCHAR(320) NOT NULL,
	token VARCHAR(128) NOT NULL,
	status VARCHAR(32) NOT NULL,
	stage VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	booked_at TIMESTAMP WITH TIME ZONE,
	scheduled_at TIMESTAMP WITH TIME ZONE,
	interviewer_metadata JSON NOT NULL,
	scheduling_metadata JSON NOT NULL,
	evaluation_status VARCHAR(32) NOT NULL,
	evaluation_ready_at TIMESTAMP WITH TIME ZONE,
	booking_url VARCHAR(1024) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_interview_sessions_token UNIQUE (token),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(outreach_event_id) REFERENCES outreach_events (id)
);
CREATE INDEX ix_interview_sessions_company_id ON interview_sessions (company_id);
CREATE INDEX ix_interview_sessions_outreach_event_id ON interview_sessions (outreach_event_id);
CREATE INDEX ix_interview_sessions_job_id ON interview_sessions (job_id);
CREATE INDEX ix_interview_sessions_candidate_id ON interview_sessions (candidate_id);
CREATE INDEX ix_interview_sessions_token ON interview_sessions (token);
CREATE INDEX ix_interview_sessions_expires ON interview_sessions (expires_at);

CREATE TABLE interviews (
	id UUID NOT NULL,
	source_app VARCHAR(32) NOT NULL,
	job_id UUID NOT NULL,
	company_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	status VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_interviews_job_candidate UNIQUE (job_id, candidate_id),
	FOREIGN KEY(job_id, candidate_id) REFERENCES candidate_profiles (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_interviews_job_id ON interviews (job_id);
CREATE INDEX ix_interviews_candidate_id ON interviews (candidate_id);
CREATE INDEX ix_interviews_company_id ON interviews (company_id);
CREATE INDEX ix_interviews_job_status ON interviews (job_id, status);

CREATE TABLE interview_evaluations (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	interviewer_id UUID,
	stage_name VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	summary TEXT NOT NULL,
	recommendation VARCHAR(32) NOT NULL,
	competency_scores JSON NOT NULL,
	notes TEXT NOT NULL,
	metadata JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_interview_evaluations_job_candidate_stage UNIQUE (job_id, candidate_id, stage_name),
	FOREIGN KEY(job_id) REFERENCES jobs (id),
	FOREIGN KEY(interviewer_id) REFERENCES users (id)
);
CREATE INDEX ix_interview_evaluations_interviewer_id ON interview_evaluations (interviewer_id);
CREATE INDEX ix_interview_evaluations_candidate_id ON interview_evaluations (candidate_id);
CREATE INDEX ix_interview_evaluations_job_id ON interview_evaluations (job_id);

CREATE TABLE orchestration_sessions (
	id UUID NOT NULL,
	session_token VARCHAR(255) NOT NULL,
	source VARCHAR(32) NOT NULL,
	current_stage VARCHAR(32) NOT NULL,
	slack_team_id VARCHAR(64) NOT NULL,
	slack_channel_id VARCHAR(64) NOT NULL,
	slack_thread_ts VARCHAR(64) NOT NULL,
	slack_user_id VARCHAR(64) NOT NULL,
	intake_mode VARCHAR(32) NOT NULL,
	selected_path VARCHAR(32) NOT NULL,
	current_question TEXT NOT NULL,
	current_question_key VARCHAR(128) NOT NULL,
	current_question_type VARCHAR(64) NOT NULL,
	current_question_schema JSON NOT NULL,
	structured_context JSON NOT NULL,
	raw_conversation JSON NOT NULL,
	normalized_intake JSON NOT NULL,
	voice_context JSON NOT NULL,
	slack_context JSON NOT NULL,
	voice_handoff_token VARCHAR(255) NOT NULL,
	voice_handoff_expires_at TIMESTAMP WITH TIME ZONE,
	voice_handoff_consumed_at TIMESTAMP WITH TIME ZONE,
	voice_token_used BOOLEAN NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	state_version INTEGER NOT NULL,
	last_processed_message_ts VARCHAR(64) NOT NULL,
	last_processed_action_hash VARCHAR(64) NOT NULL,
	last_processed_transcript_hash VARCHAR(64) NOT NULL,
	intake_version VARCHAR(32) NOT NULL,
	company_id UUID,
	job_id UUID,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_orchestration_sessions_session_token UNIQUE (session_token),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_orchestration_sessions_session_token ON orchestration_sessions (session_token);
CREATE INDEX ix_orchestration_sessions_job_id ON orchestration_sessions (job_id);
CREATE INDEX ix_orchestration_sessions_company_id ON orchestration_sessions (company_id);

CREATE TABLE orchestration_events (
	id UUID NOT NULL,
	session_id UUID NOT NULL,
	event_type VARCHAR(64) NOT NULL,
	event_payload JSON NOT NULL,
	source VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES orchestration_sessions (id)
);
CREATE INDEX ix_orchestration_events_session_id ON orchestration_events (session_id);

CREATE TABLE automation_jobs (
	id UUID NOT NULL,
	job_id UUID,
	candidate_id VARCHAR(128),
	automation_type VARCHAR(64) NOT NULL,
	automation_key VARCHAR(255) NOT NULL,
	status VARCHAR(32) NOT NULL,
	scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	attempt_count INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	last_error TEXT NOT NULL,
	automation_payload JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_automation_jobs_automation_key UNIQUE (automation_key),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_automation_jobs_job_id ON automation_jobs (job_id);
CREATE INDEX ix_automation_jobs_candidate_id ON automation_jobs (candidate_id);

CREATE TABLE audit_events (
	id UUID NOT NULL,
	actor_id UUID,
	actor_type VARCHAR(32) NOT NULL,
	action VARCHAR(128) NOT NULL,
	entity_type VARCHAR(128) NOT NULL,
	entity_id VARCHAR(128) NOT NULL,
	metadata JSON NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	user_agent VARCHAR(512) NOT NULL,
	request_id VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);
CREATE INDEX ix_audit_events_action ON audit_events (action);
CREATE INDEX ix_audit_events_entity_id ON audit_events (entity_id);
CREATE INDEX ix_audit_events_entity_type ON audit_events (entity_type);

CREATE TABLE embedding_version_registry (
	id UUID NOT NULL,
	embedding_version VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	vector_size INTEGER NOT NULL,
	details JSON NOT NULL,
	activated_at TIMESTAMP WITH TIME ZONE,
	retired_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_embedding_version_registry_version UNIQUE (embedding_version)
);
CREATE INDEX ix_embedding_version_registry_embedding_version ON embedding_version_registry (embedding_version);

CREATE TABLE ats_exports (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	candidate_id VARCHAR(128),
	candidate_ids JSON NOT NULL,
	provider VARCHAR(64) NOT NULL,
	status VARCHAR(64) NOT NULL,
	external_reference VARCHAR(255) NOT NULL,
	error TEXT NOT NULL,
	response_payload JSON NOT NULL,
	exported_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ats_exports_job_candidate_provider UNIQUE (job_id, candidate_id, provider),
	FOREIGN KEY(job_id, candidate_id) REFERENCES candidate_profiles (job_id, candidate_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_ats_exports_job_id ON ats_exports (job_id);
CREATE INDEX ix_ats_exports_candidate_id ON ats_exports (candidate_id);

CREATE TABLE ats_export_retries (
	id UUID NOT NULL,
	job_id UUID NOT NULL,
	candidate_ids JSON NOT NULL,
	provider VARCHAR(64) NOT NULL,
	attempt_count INTEGER NOT NULL,
	last_error TEXT NOT NULL,
	next_retry_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_ats_export_retries_job_id ON ats_export_retries (job_id);

-- Requested table names that are not defined in the current SQLAlchemy model set:
-- agencies
-- agency_integrations
-- auth_otps
-- recruiter_preference_snapshots
-- search_sessions
-- token_redemptions
-- transcripts
