--
-- PostgreSQL database dump
--

\restrict fW54uIwzCFvJbsULoodHuY5PK8hhMGPKjw1X7W6RshFotZaDCNkErvz2nVCFlGW

-- Dumped from database version 18.4 (Debian 18.4-1.pgdg13+1)
-- Dumped by pg_dump version 18.4 (Debian 18.4-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: postgres
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO postgres;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA public IS '';


--
-- Name: pageinspect; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pageinspect WITH SCHEMA public;


--
-- Name: EXTENSION pageinspect; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pageinspect IS 'inspect the contents of database pages at a low level';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: candidatestage; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.candidatestage AS ENUM (
    'APPLIED',
    'SHORTLISTED',
    'RESUME_REJECTED',
    'REVIEW',
    'INTERVIEW_SCHEDULED',
    'INTERVIEW_RESCHEDULED',
    'INTERVIEWED',
    'NO_SHOW',
    'SELECTED',
    'REJECTED'
);


ALTER TYPE public.candidatestage OWNER TO postgres;

--
-- Name: parsingstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.parsingstatus AS ENUM (
    'PENDING',
    'PROCESSING',
    'COMPLETED',
    'FAILED'
);


ALTER TYPE public.parsingstatus OWNER TO postgres;

--
-- Name: reviewstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.reviewstatus AS ENUM (
    'UNASSIGNED',
    'PENDING',
    'INTERVIEW_INVITED',
    'REJECTED'
);


ALTER TYPE public.reviewstatus OWNER TO postgres;

--
-- Name: transactiontype; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.transactiontype AS ENUM (
    'CREDIT',
    'DEBIT'
);


ALTER TYPE public.transactiontype OWNER TO postgres;

--
-- Name: userrole; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.userrole AS ENUM (
    'super_admin',
    'admin',
    'recruiter',
    'hiring_manager',
    'viewer'
);


ALTER TYPE public.userrole OWNER TO postgres;

--
-- Name: activity_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.activity_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.activity_logs_id_seq OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: activity_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.activity_logs (
    id bigint DEFAULT nextval('public.activity_logs_id_seq'::regclass) NOT NULL,
    action text,
    entity_type text,
    entity_id bigint,
    details json,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    agency_id uuid,
    user_id uuid
);


ALTER TABLE public.activity_logs OWNER TO postgres;

--
-- Name: agencies; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agencies (
    name text NOT NULL,
    slug text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    new_id uuid DEFAULT gen_random_uuid(),
    id uuid DEFAULT gen_random_uuid() CONSTRAINT agencies_uuid_not_null NOT NULL
);


ALTER TABLE public.agencies OWNER TO postgres;

--
-- Name: agency_discounts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agency_discounts (
    id integer NOT NULL,
    agency_id uuid NOT NULL,
    discount_type character varying(20) NOT NULL,
    discount_value double precision NOT NULL,
    currency character varying(10),
    set_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone
);


ALTER TABLE public.agency_discounts OWNER TO postgres;

--
-- Name: agency_discounts_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agency_discounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.agency_discounts_id_seq OWNER TO postgres;

--
-- Name: agency_discounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agency_discounts_id_seq OWNED BY public.agency_discounts.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: allowed_users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.allowed_users (
    id uuid NOT NULL,
    email character varying(255) NOT NULL,
    added_by uuid,
    note text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.allowed_users OWNER TO postgres;

--
-- Name: analytics_widgets_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.analytics_widgets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.analytics_widgets_id_seq OWNER TO postgres;

--
-- Name: analytics_widgets; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.analytics_widgets (
    id bigint DEFAULT nextval('public.analytics_widgets_id_seq'::regclass) NOT NULL,
    widget_name text,
    metric_key text,
    role_access text,
    is_default boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.analytics_widgets OWNER TO postgres;

--
-- Name: ats_export_retries; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ats_export_retries (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    candidate_ids jsonb NOT NULL,
    provider character varying(64) NOT NULL,
    attempt_count integer NOT NULL,
    last_error text NOT NULL,
    next_retry_at timestamp with time zone NOT NULL,
    status character varying(32) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.ats_export_retries OWNER TO postgres;

--
-- Name: ats_exports; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ats_exports (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    candidate_id character varying(128),
    candidate_ids jsonb NOT NULL,
    provider character varying(64) NOT NULL,
    status character varying(64) NOT NULL,
    external_reference character varying(255) NOT NULL,
    error text NOT NULL,
    response_payload jsonb NOT NULL,
    exported_at timestamp with time zone NOT NULL
);


ALTER TABLE public.ats_exports OWNER TO postgres;

--
-- Name: audit_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.audit_events (
    id uuid NOT NULL,
    actor_id uuid,
    actor_type character varying(32) NOT NULL,
    action character varying(128) NOT NULL,
    entity_type character varying(128) NOT NULL,
    entity_id character varying(128) NOT NULL,
    metadata json NOT NULL,
    ip_address character varying(64) NOT NULL,
    user_agent character varying(512) NOT NULL,
    request_id character varying(128) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    agency_id uuid,
    user_id uuid,
    slack_user_id character varying(64) DEFAULT ''::character varying,
    action_type character varying(128) DEFAULT ''::character varying,
    payload json DEFAULT '{}'::json
);


ALTER TABLE public.audit_events OWNER TO postgres;

--
-- Name: automation_jobs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.automation_jobs (
    id uuid NOT NULL,
    job_id uuid,
    candidate_id character varying(128),
    automation_type character varying(64) NOT NULL,
    automation_key character varying(255) NOT NULL,
    status character varying(32) NOT NULL,
    scheduled_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    last_error text NOT NULL,
    automation_payload json NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.automation_jobs OWNER TO postgres;

--
-- Name: booking_links; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.booking_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token text DEFAULT (gen_random_uuid())::text NOT NULL,
    agency_id uuid,
    candidate_id uuid,
    job_id uuid,
    user_id uuid,
    expires_at timestamp with time zone DEFAULT (now() + '72:00:00'::interval) NOT NULL,
    used boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.booking_links OWNER TO postgres;

--
-- Name: candidate_feedback; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.candidate_feedback (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    candidate_id character varying(128) NOT NULL,
    feedback character varying(16) NOT NULL,
    accepted boolean DEFAULT false NOT NULL,
    rejected boolean DEFAULT false NOT NULL,
    recruiter_id uuid,
    session_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    slack_installation_id uuid,
    slack_team_id character varying(64) DEFAULT ''::character varying NOT NULL,
    slack_user_id character varying(64) DEFAULT ''::character varying NOT NULL,
    agency_id uuid
);


ALTER TABLE public.candidate_feedback OWNER TO postgres;

--
-- Name: candidate_lifecycle_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.candidate_lifecycle_events (
    id uuid NOT NULL,
    agency_id uuid NOT NULL,
    job_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    from_status character varying(50),
    to_status character varying(50),
    source character varying(50),
    actor_id uuid,
    transition_key character varying(100),
    event_metadata jsonb,
    slack_installation_id uuid,
    slack_team_id character varying(100),
    slack_user_id character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.candidate_lifecycle_events OWNER TO postgres;

--
-- Name: candidate_selection_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.candidate_selection_sessions (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    status character varying(32) NOT NULL,
    current_batch_index integer NOT NULL,
    batch_size integer NOT NULL,
    total_batches integer NOT NULL,
    candidate_pool_snapshot jsonb NOT NULL,
    batch_plan jsonb NOT NULL,
    selected_candidate_ids jsonb NOT NULL,
    rejected_candidate_ids jsonb NOT NULL,
    batch_history jsonb NOT NULL,
    selection_analysis jsonb NOT NULL,
    final_candidate_snapshot jsonb NOT NULL,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.candidate_selection_sessions OWNER TO postgres;

--
-- Name: candidates; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.candidates (
    candidate_id text,
    name text,
    email text,
    phone text,
    current_company text,
    "current_role" text,
    experience_years double precision,
    location text,
    linkedin_url text,
    resume_file_path text,
    resume_text text,
    parsing_status text,
    resume_score double precision,
    score_threshold double precision,
    skills json,
    education json,
    work_experience json,
    stage text,
    stage_updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    source text,
    decline_reason text,
    offer_status text,
    synced_to_sheets boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    summary text,
    display_status text,
    predefined_questions text,
    interview_transcript text,
    interview_ai_summary text,
    interview_technical_score double precision,
    interview_communication_score double precision,
    interview_culture_fit_score double precision,
    interview_video_url text,
    stage_entered_at timestamp with time zone,
    applied_at timestamp with time zone,
    internal_notes text,
    review_status text DEFAULT 'unassigned'::text,
    reviewed_at timestamp without time zone,
    reviewed_by_user_id uuid,
    id uuid DEFAULT gen_random_uuid() CONSTRAINT candidates_uuid_not_null NOT NULL,
    assigned_to_user_id uuid,
    created_by uuid,
    agency_id uuid,
    job_id uuid,
    created_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL,
    updated_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL,
    raw_data jsonb,
    candidate_status character varying(64),
    resume_received_at timestamp with time zone,
    github_url character varying(500),
    parsed_resume_json jsonb,
    parsed_resume_text text,
    fit_score double precision,
    decision character varying(32),
    strategy character varying(64),
    last_scored_at timestamp with time zone,
    last_refreshed_at timestamp with time zone,
    ats_status character varying(64),
    ats_status_source character varying(64),
    ats_status_reason text,
    ats_status_updated_at timestamp with time zone,
    ats_metadata jsonb,
    workflow_token character varying(255)
);


ALTER TABLE public.candidates OWNER TO postgres;

--
-- Name: candidates_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.candidates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.candidates_id_seq OWNER TO postgres;

--
-- Name: clients; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.clients (
    company_name text,
    industry text,
    contact_person text,
    contact_email text,
    contact_phone text,
    total_positions bigint,
    positions_filled bigint,
    positions_open bigint,
    avg_time_to_hire double precision,
    retention_rate double precision,
    acceptance_rate double precision,
    is_active boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() CONSTRAINT clients_uuid_not_null NOT NULL,
    agency_id uuid
);


ALTER TABLE public.clients OWNER TO postgres;

--
-- Name: clients_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.clients_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.clients_id_seq OWNER TO postgres;

--
-- Name: email_communications_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.email_communications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.email_communications_id_seq OWNER TO postgres;

--
-- Name: email_communications; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.email_communications (
    id bigint DEFAULT nextval('public.email_communications_id_seq'::regclass) NOT NULL,
    candidate_name text,
    candidate_email text,
    email_type text,
    status text,
    sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    candidate_id uuid,
    agency_id uuid,
    template_id integer,
    subject character varying(500),
    body text,
    trigger_stage character varying(100),
    trigger_source character varying(100),
    error_message text,
    placeholder_payload json,
    workflow_token character varying(255),
    provider_message_id character varying(255),
    cc_email character varying(255),
    responded_at timestamp with time zone,
    slot_booked_at timestamp with time zone
);


ALTER TABLE public.email_communications OWNER TO postgres;

--
-- Name: email_templates_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.email_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.email_templates_id_seq OWNER TO postgres;

--
-- Name: email_templates; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.email_templates (
    id bigint DEFAULT nextval('public.email_templates_id_seq'::regclass) NOT NULL,
    name text,
    subject text,
    body text,
    template_type text,
    variables json,
    is_active boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    agency_id uuid,
    created_by_user_id uuid,
    automation_enabled boolean DEFAULT false,
    trigger_stage character varying(100),
    description text,
    status character varying(100) DEFAULT 'resume_shortlisted'::character varying,
    is_html boolean DEFAULT true,
    is_default boolean DEFAULT false,
    is_selected boolean DEFAULT false
);


ALTER TABLE public.email_templates OWNER TO postgres;

--
-- Name: embedding_version_registry; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.embedding_version_registry (
    id uuid NOT NULL,
    embedding_version character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    vector_size integer NOT NULL,
    details json NOT NULL,
    activated_at timestamp with time zone,
    retired_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.embedding_version_registry OWNER TO postgres;

--
-- Name: enterprise_leads; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.enterprise_leads (
    id integer NOT NULL,
    agency_id uuid,
    company_name character varying(255) NOT NULL,
    contact_name character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    phone character varying(50),
    expected_users integer,
    notes text,
    source character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.enterprise_leads OWNER TO postgres;

--
-- Name: enterprise_leads_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.enterprise_leads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.enterprise_leads_id_seq OWNER TO postgres;

--
-- Name: enterprise_leads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.enterprise_leads_id_seq OWNED BY public.enterprise_leads.id;


--
-- Name: feed_access_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.feed_access_logs (
    id uuid NOT NULL,
    portal_name character varying(100) NOT NULL,
    ip_address character varying(255),
    user_agent character varying(1000),
    accessed_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.feed_access_logs OWNER TO postgres;

--
-- Name: inbound_email_attachments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.inbound_email_attachments (
    id uuid NOT NULL,
    reply_id uuid NOT NULL,
    provider_attachment_id character varying(255) NOT NULL,
    filename character varying(512) NOT NULL,
    content_type character varying(255) NOT NULL,
    size_bytes integer NOT NULL,
    storage_path character varying(1024) NOT NULL,
    public_url character varying(1024) NOT NULL,
    sha256 character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.inbound_email_attachments OWNER TO postgres;

--
-- Name: inbound_email_replies; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.inbound_email_replies (
    id uuid NOT NULL,
    svix_id character varying(255) NOT NULL,
    event_type character varying(64) NOT NULL,
    email_id character varying(255) NOT NULL,
    provider_message_id character varying(255) NOT NULL,
    candidate_id character varying(128),
    job_id uuid,
    agency_id uuid,
    outreach_event_id uuid,
    sender_email character varying(320) NOT NULL,
    sender_name character varying(255) NOT NULL,
    subject character varying(255) NOT NULL,
    body_text text NOT NULL,
    body_html text NOT NULL,
    received_at timestamp with time zone NOT NULL,
    webhook_created_at timestamp with time zone,
    processing_status character varying(32) NOT NULL,
    match_status character varying(32) NOT NULL,
    intent character varying(64) NOT NULL,
    attachment_count integer NOT NULL,
    raw_payload jsonb NOT NULL,
    processing_error text NOT NULL,
    processed_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.inbound_email_replies OWNER TO postgres;

--
-- Name: interview_evaluations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interview_evaluations (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    candidate_id character varying(128) NOT NULL,
    interviewer_id uuid,
    stage_name character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    summary text NOT NULL,
    recommendation character varying(32) NOT NULL,
    competency_scores jsonb NOT NULL,
    notes text NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.interview_evaluations OWNER TO postgres;

--
-- Name: interview_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interview_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agency_id uuid,
    job_id uuid,
    candidate_id uuid,
    user_id uuid,
    slot_id uuid,
    candidate_name text,
    email text,
    job_role text,
    jd_text text,
    resume_text text,
    session_token text NOT NULL,
    status text DEFAULT 'scheduled'::text,
    created_at timestamp with time zone DEFAULT now(),
    recording_path text,
    vapi_recording_url text,
    recording_size_bytes integer,
    last_transcript_snapshot text,
    recording_data bytea,
    interview_questions jsonb,
    skills text,
    agency_name text,
    recording_format text DEFAULT 'webm'::text,
    recording_duration_seconds integer DEFAULT 0,
    recording_created_at timestamp with time zone,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    last_activity_at timestamp with time zone,
    disconnected_at timestamp with time zone,
    connection_status text,
    vapi_call_id text,
    vapi_conversation_state jsonb,
    recording_status text,
    recording_status_detail text,
    recording_status_updated_at timestamp with time zone,
    recording_status_events jsonb DEFAULT '[]'::jsonb NOT NULL,
    recording_error text,
    ai_summary text,
    end_reason text,
    reconnect_deadline_at timestamp without time zone,
    recording_finalized_at timestamp with time zone,
    booking_token text,
    booked_at timestamp with time zone,
    last_booked_at timestamp with time zone,
    proctoring_events jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL,
    updated_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL
);


ALTER TABLE public.interview_sessions OWNER TO postgres;

--
-- Name: interview_share_access_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interview_share_access_logs (
    id bigint NOT NULL,
    share_token character varying(255),
    access_type character varying(50),
    ip_address character varying(255),
    user_agent text,
    accessed_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.interview_share_access_logs OWNER TO postgres;

--
-- Name: interview_share_access_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.interview_share_access_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.interview_share_access_logs_id_seq OWNER TO postgres;

--
-- Name: interview_share_access_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.interview_share_access_logs_id_seq OWNED BY public.interview_share_access_logs.id;


--
-- Name: interview_share_links; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interview_share_links (
    id bigint NOT NULL,
    interview_id uuid NOT NULL,
    share_token character varying(255) NOT NULL,
    created_by character varying(255),
    expires_at timestamp without time zone NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    password_hash text,
    last_viewed_at timestamp with time zone,
    view_count integer DEFAULT 0 NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    revoked_at timestamp with time zone
);


ALTER TABLE public.interview_share_links OWNER TO postgres;

--
-- Name: interview_share_links_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.interview_share_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.interview_share_links_id_seq OWNER TO postgres;

--
-- Name: interview_share_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.interview_share_links_id_seq OWNED BY public.interview_share_links.id;


--
-- Name: interview_slots; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interview_slots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agency_id uuid,
    slot_date date NOT NULL,
    slot_time time without time zone NOT NULL,
    max_concurrent integer DEFAULT 3,
    current_bookings integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.interview_slots OWNER TO postgres;

--
-- Name: interviews; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.interviews (
    interview_type text,
    scheduled_at timestamp with time zone,
    duration_minutes bigint,
    meeting_link text,
    status text,
    video_url text,
    transcript text,
    ai_summary text,
    interview_score double precision,
    feedback text,
    interviewer_notes text,
    technical_score double precision,
    communication_score double precision,
    culture_fit_score double precision,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    is_async boolean DEFAULT false,
    async_link text,
    async_token text,
    async_expires_at timestamp with time zone,
    async_started_at timestamp with time zone,
    async_completed_at timestamp with time zone,
    async_answers json,
    id uuid DEFAULT gen_random_uuid() CONSTRAINT interviews_uuid_not_null NOT NULL,
    candidate_id uuid,
    agency_id uuid,
    interview_score_reason text,
    technical_score_reason text,
    communication_score_reason text,
    culture_fit_score_reason text,
    created_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL,
    updated_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL
);


ALTER TABLE public.interviews OWNER TO postgres;

--
-- Name: interviews_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.interviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.interviews_id_seq OWNER TO postgres;

--
-- Name: job_applications; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_applications (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    full_name character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    phone character varying(50),
    resume_url character varying(1000),
    cover_letter text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    original_filename character varying(255),
    stored_filename character varying(255),
    uploaded_at timestamp with time zone DEFAULT now(),
    linkedin_url text,
    source character varying(50) DEFAULT 'Career Portal'::character varying,
    status character varying(30) DEFAULT 'Pending'::character varying,
    resume_content_type character varying(100),
    resume_text text,
    job_role character varying(255)
);


ALTER TABLE public.job_applications OWNER TO postgres;

--
-- Name: job_descriptions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_descriptions (
    job_id text,
    title text,
    company_name text,
    department text,
    location text,
    employment_type text,
    experience_required text,
    salary_range text,
    vacancies bigint,
    description text,
    requirements text,
    responsibilities text,
    skills json,
    is_active boolean,
    status text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    interview_questions json,
    min_passing_score bigint DEFAULT '60'::bigint,
    id uuid DEFAULT gen_random_uuid() CONSTRAINT job_descriptions_uuid_not_null NOT NULL,
    agency_id uuid,
    city character varying(120),
    state character varying(120),
    country character varying(120),
    category character varying(150),
    remote boolean DEFAULT false,
    company_website_url character varying(500),
    company_logo_url character varying(1000),
    industry character varying(255),
    valid_through timestamp with time zone,
    shortlist_email_template_type character varying(50),
    shortlist_email_template_html text,
    email_template_id bigint,
    created_by uuid,
    created_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL,
    updated_by_source character varying(30) DEFAULT 'PONTIS'::character varying NOT NULL
);


ALTER TABLE public.job_descriptions OWNER TO postgres;

--
-- Name: job_descriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.job_descriptions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.job_descriptions_id_seq OWNER TO postgres;

--
-- Name: job_distribution_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_distribution_logs (
    id uuid NOT NULL,
    job_id uuid,
    portal_name character varying(100) NOT NULL,
    status character varying(50) NOT NULL,
    message text,
    posted_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.job_distribution_logs OWNER TO postgres;

--
-- Name: job_intakes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_intakes (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    agency_id uuid NOT NULL,
    transcript text NOT NULL,
    structured_data_json jsonb NOT NULL,
    intake_status character varying(32) NOT NULL,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.job_intakes OWNER TO postgres;

--
-- Name: job_portal_mapping; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_portal_mapping (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    portal_name character varying(100) NOT NULL
);


ALTER TABLE public.job_portal_mapping OWNER TO postgres;

--
-- Name: job_portals; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.job_portals (
    id uuid NOT NULL,
    portal_name character varying(100) NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    feed_url character varying(500) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.job_portals OWNER TO postgres;

--
-- Name: notification_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.notification_events (
    id uuid NOT NULL,
    job_id uuid,
    agency_id uuid,
    candidate_id character varying(128),
    actor_id uuid,
    recipient_type character varying(32) NOT NULL,
    recipient character varying(255) NOT NULL,
    channel character varying(32) NOT NULL,
    title character varying(255) NOT NULL,
    body text NOT NULL,
    status character varying(32) NOT NULL,
    notification_type character varying(64) NOT NULL,
    notification_key character varying(255) NOT NULL,
    delivery_reference character varying(255),
    notification_metadata jsonb NOT NULL,
    delivered_at timestamp with time zone,
    failed_at timestamp with time zone,
    read_at timestamp with time zone,
    is_read boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.notification_events OWNER TO postgres;

--
-- Name: notification_workflow_tokens; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.notification_workflow_tokens (
    id uuid NOT NULL,
    agency_id uuid,
    candidate_id uuid,
    job_id uuid,
    user_id uuid,
    token character varying(255) NOT NULL,
    token_type character varying(100) NOT NULL,
    payload json NOT NULL,
    is_active boolean,
    expires_at timestamp with time zone,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone,
    booked_at timestamp with time zone,
    rebook_until timestamp with time zone
);


ALTER TABLE public.notification_workflow_tokens OWNER TO postgres;

--
-- Name: orchestration_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.orchestration_events (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    event_type character varying(64) NOT NULL,
    event_payload jsonb NOT NULL,
    source character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.orchestration_events OWNER TO postgres;

--
-- Name: orchestration_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.orchestration_sessions (
    id uuid NOT NULL,
    session_token character varying(255) NOT NULL,
    source character varying(32) NOT NULL,
    current_stage character varying(32) NOT NULL,
    slack_team_id character varying(64) NOT NULL,
    slack_channel_id character varying(64) NOT NULL,
    slack_thread_ts character varying(64) NOT NULL,
    slack_user_id character varying(64) NOT NULL,
    intake_mode character varying(32) NOT NULL,
    selected_path character varying(32) NOT NULL,
    current_question text NOT NULL,
    current_question_key character varying(128) NOT NULL,
    current_question_type character varying(64) NOT NULL,
    current_question_schema jsonb NOT NULL,
    structured_context jsonb NOT NULL,
    raw_conversation jsonb NOT NULL,
    normalized_intake jsonb NOT NULL,
    voice_context jsonb NOT NULL,
    slack_context jsonb NOT NULL,
    voice_handoff_token character varying(255) NOT NULL,
    voice_handoff_expires_at timestamp with time zone,
    voice_handoff_consumed_at timestamp with time zone,
    voice_token_used boolean NOT NULL,
    expires_at timestamp with time zone,
    completed_at timestamp with time zone,
    state_version integer NOT NULL,
    last_processed_message_ts character varying(64) NOT NULL,
    last_processed_action_hash character varying(64) NOT NULL,
    last_processed_transcript_hash character varying(64) NOT NULL,
    intake_version character varying(32) NOT NULL,
    agency_id uuid,
    job_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.orchestration_sessions OWNER TO postgres;

--
-- Name: otps; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.otps (
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    otp_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.otps OWNER TO postgres;

--
-- Name: outreach_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.outreach_events (
    id uuid NOT NULL,
    source_app character varying(32) NOT NULL,
    job_id uuid NOT NULL,
    agency_id uuid NOT NULL,
    candidate_id character varying(128) NOT NULL,
    provider character varying(64) NOT NULL,
    to_email character varying(320) NOT NULL,
    subject character varying(255) NOT NULL,
    body text NOT NULL,
    status character varying(64) NOT NULL,
    reply_state character varying(64) NOT NULL,
    archive_reason character varying(255) NOT NULL,
    attempt_count integer NOT NULL,
    follow_up_count integer NOT NULL,
    open_count integer NOT NULL,
    reply_count integer NOT NULL,
    provider_message_id character varying(255),
    last_error text NOT NULL,
    sent_at timestamp with time zone,
    last_sent_at timestamp with time zone,
    last_contacted_at timestamp with time zone,
    last_opened_at timestamp with time zone,
    last_replied_at timestamp with time zone,
    next_follow_up_at timestamp with time zone,
    message_text text NOT NULL,
    resume_url character varying(500) NOT NULL,
    reply_intent character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.outreach_events OWNER TO postgres;

--
-- Name: plan_limits; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.plan_limits (
    id integer NOT NULL,
    plan_id uuid NOT NULL,
    feature_name character varying(100) NOT NULL,
    total_limit integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.plan_limits OWNER TO postgres;

--
-- Name: plan_limits_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.plan_limits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.plan_limits_id_seq OWNER TO postgres;

--
-- Name: plan_limits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.plan_limits_id_seq OWNED BY public.plan_limits.id;


--
-- Name: plans; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.plans (
    id uuid NOT NULL,
    name character varying(100) NOT NULL,
    price double precision,
    duration character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    plan_name character varying(100),
    billing_cycle character varying(20),
    base_price double precision,
    credits integer,
    seat_limit integer,
    job_posting_limit integer,
    feature_config json
);


ALTER TABLE public.plans OWNER TO postgres;

--
-- Name: ranking_explanations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ranking_explanations (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    agency_id uuid NOT NULL,
    candidate_id character varying(128) NOT NULL,
    existing_score double precision NOT NULL,
    recruiter_score double precision NOT NULL,
    session_signal double precision NOT NULL,
    final_score double precision NOT NULL,
    recruiter_capped boolean NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ranking_explanations OWNER TO postgres;

--
-- Name: ranking_runs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ranking_runs (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    recruiter_id uuid,
    run_type character varying(32) NOT NULL,
    avg_existing_score double precision NOT NULL,
    avg_final_score double precision NOT NULL,
    avg_recruiter_score double precision NOT NULL,
    percent_recruiter_capped double precision NOT NULL,
    candidate_count integer NOT NULL,
    drift_delta double precision NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.ranking_runs OWNER TO postgres;

--
-- Name: recording_retry_queue; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recording_retry_queue (
    id integer NOT NULL,
    session_token text NOT NULL,
    file_name text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recording_retry_queue OWNER TO postgres;

--
-- Name: recording_retry_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.recording_retry_queue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.recording_retry_queue_id_seq OWNER TO postgres;

--
-- Name: recording_retry_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.recording_retry_queue_id_seq OWNED BY public.recording_retry_queue.id;


--
-- Name: recruiter_experience_preferences; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recruiter_experience_preferences (
    id uuid NOT NULL,
    recruiter_id uuid NOT NULL,
    experience_bucket character varying(100) NOT NULL,
    weight double precision NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recruiter_experience_preferences OWNER TO postgres;

--
-- Name: recruiter_notes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recruiter_notes (
    id character varying(36) NOT NULL,
    job_id character varying(36) NOT NULL,
    candidate_id character varying(128),
    recruiter_id character varying(36),
    note_type character varying(32) DEFAULT 'note'::character varying NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recruiter_notes OWNER TO postgres;

--
-- Name: recruiter_role_preferences; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recruiter_role_preferences (
    id uuid NOT NULL,
    recruiter_id uuid NOT NULL,
    role character varying(255) NOT NULL,
    weight double precision NOT NULL,
    positive_count integer NOT NULL,
    negative_count integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recruiter_role_preferences OWNER TO postgres;

--
-- Name: recruiter_skill_preferences; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recruiter_skill_preferences (
    id uuid NOT NULL,
    recruiter_id uuid NOT NULL,
    skill character varying(255) NOT NULL,
    weight double precision NOT NULL,
    positive_count integer NOT NULL,
    negative_count integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recruiter_skill_preferences OWNER TO postgres;

--
-- Name: recruiter_tasks; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recruiter_tasks (
    id character varying(36) NOT NULL,
    job_id character varying(36) NOT NULL,
    candidate_id character varying(128),
    recruiter_id character varying(36),
    title character varying(255) DEFAULT ''::character varying NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    status character varying(32) DEFAULT 'open'::character varying NOT NULL,
    priority character varying(16) DEFAULT 'normal'::character varying NOT NULL,
    due_at timestamp with time zone,
    completed_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.recruiter_tasks OWNER TO postgres;

--
-- Name: scoring_profiles; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.scoring_profiles (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    weight_pdl double precision NOT NULL,
    weight_semantic double precision NOT NULL,
    weight_skill double precision NOT NULL,
    weight_recency double precision NOT NULL,
    feedback_bias double precision NOT NULL,
    elite_reasoning_bonus double precision NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.scoring_profiles OWNER TO postgres;

--
-- Name: slack_installations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.slack_installations (
    id uuid NOT NULL,
    agency_id uuid NOT NULL,
    team_id character varying(64) NOT NULL,
    team_name character varying(255) DEFAULT ''::character varying NOT NULL,
    enterprise_id character varying(64) DEFAULT ''::character varying NOT NULL,
    bot_user_id character varying(64) DEFAULT ''::character varying NOT NULL,
    bot_access_token text DEFAULT ''::text NOT NULL,
    scope_list jsonb DEFAULT '[]'::jsonb NOT NULL,
    installed_by_user_id uuid,
    installed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    revoked_at timestamp with time zone,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.slack_installations OWNER TO postgres;

--
-- Name: slack_users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.slack_users (
    id uuid NOT NULL,
    agency_id uuid NOT NULL,
    slack_installation_id uuid NOT NULL,
    slack_user_id character varying(64) NOT NULL,
    email character varying(320) DEFAULT ''::character varying NOT NULL,
    display_name character varying(255) DEFAULT ''::character varying NOT NULL,
    internal_user_id uuid,
    role character varying(32) DEFAULT 'recruiter'::character varying NOT NULL,
    first_seen_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_seen_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.slack_users OWNER TO postgres;

--
-- Name: subscription_activity_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.subscription_activity_logs (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    action_type character varying(50) NOT NULL,
    previous_value character varying(255),
    added_value character varying(255),
    new_value character varying(255),
    performed_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.subscription_activity_logs OWNER TO postgres;

--
-- Name: subscription_activity_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.subscription_activity_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.subscription_activity_logs_id_seq OWNER TO postgres;

--
-- Name: subscription_activity_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.subscription_activity_logs_id_seq OWNED BY public.subscription_activity_logs.id;


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.subscriptions (
    id integer NOT NULL,
    legacy_user_id integer,
    plan_name character varying(50) NOT NULL,
    billing_type character varying(20) NOT NULL,
    price_per_user numeric(10,2),
    total_price numeric(10,2),
    interview_credits_total integer DEFAULT 0,
    interview_credits_used integer DEFAULT 0,
    max_job_posts integer DEFAULT 0,
    used_job_posts integer DEFAULT 0,
    max_users integer DEFAULT 0,
    current_users integer DEFAULT 0,
    resume_scoring_limit integer DEFAULT 0,
    resume_scoring_used integer DEFAULT 0,
    is_unlimited_resume_scoring boolean DEFAULT false,
    is_unlimited_jobs boolean DEFAULT false,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    cycle_anchor_at timestamp with time zone DEFAULT now(),
    last_monthly_reset_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone,
    user_id uuid,
    plan_id uuid,
    status character varying(50) DEFAULT 'active'::character varying,
    agency_id uuid,
    payment_provider character varying(50),
    payment_status character varying(50),
    razorpay_order_id character varying(255),
    razorpay_payment_id character varying(255),
    razorpay_signature character varying(255),
    payment_payload json,
    previous_subscription_id integer,
    original_price double precision,
    discount_percent double precision,
    discount_amount double precision,
    custom_price_override double precision,
    final_price double precision,
    interview_topup_total integer DEFAULT 0,
    interview_topup_used integer DEFAULT 0 NOT NULL,
    trial_start_at timestamp with time zone,
    trial_end_at timestamp with time zone,
    trial_status character varying(50),
    admin_seat_limit integer,
    user_seat_limit integer,
    user_count integer
);


ALTER TABLE public.subscriptions OWNER TO postgres;

--
-- Name: subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.subscriptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.subscriptions_id_seq OWNER TO postgres;

--
-- Name: subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.subscriptions_id_seq OWNED BY public.subscriptions.id;


--
-- Name: usage_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.usage_logs (
    id integer NOT NULL,
    agency_id uuid NOT NULL,
    subscription_id integer NOT NULL,
    wallet_id integer NOT NULL,
    user_id uuid,
    feature_name character varying(100) NOT NULL,
    action character varying(50) NOT NULL,
    amount integer NOT NULL,
    before_used integer,
    after_used integer,
    before_remaining integer,
    after_remaining integer,
    details json,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.usage_logs OWNER TO postgres;

--
-- Name: usage_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.usage_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.usage_logs_id_seq OWNER TO postgres;

--
-- Name: usage_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.usage_logs_id_seq OWNED BY public.usage_logs.id;


--
-- Name: usage_tracking; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.usage_tracking (
    id integer NOT NULL,
    user_id uuid NOT NULL,
    feature_name character varying(100) NOT NULL,
    used_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.usage_tracking OWNER TO postgres;

--
-- Name: usage_tracking_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.usage_tracking_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.usage_tracking_id_seq OWNER TO postgres;

--
-- Name: usage_tracking_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.usage_tracking_id_seq OWNED BY public.usage_tracking.id;


--
-- Name: user_dashboard_preferences_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.user_dashboard_preferences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_dashboard_preferences_id_seq OWNER TO postgres;

--
-- Name: user_dashboard_preferences; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_dashboard_preferences (
    id bigint DEFAULT nextval('public.user_dashboard_preferences_id_seq'::regclass) NOT NULL,
    widget_id bigint,
    "position" bigint,
    size text,
    is_enabled boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    user_id uuid
);


ALTER TABLE public.user_dashboard_preferences OWNER TO postgres;

--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    email text,
    hashed_password text,
    full_name text,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone,
    phone text,
    department text,
    bio text,
    avatar_url text,
    last_login_at timestamp without time zone,
    wallet_balance real DEFAULT '0'::real,
    id uuid DEFAULT gen_random_uuid() CONSTRAINT users_uuid_not_null NOT NULL,
    agency_id uuid
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO postgres;

--
-- Name: wallet_transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.wallet_transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wallet_transactions_id_seq OWNER TO postgres;

--
-- Name: wallet_transactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.wallet_transactions (
    id bigint DEFAULT nextval('public.wallet_transactions_id_seq'::regclass) NOT NULL,
    amount double precision,
    transaction_type text,
    description text,
    balance_after double precision,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    agency_id uuid,
    user_id uuid,
    provider character varying(50),
    razorpay_order_id character varying(255),
    razorpay_payment_id character varying(255),
    razorpay_signature character varying(255),
    payment_status character varying(50),
    provider_payload json
);


ALTER TABLE public.wallet_transactions OWNER TO postgres;

--
-- Name: wallets; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.wallets (
    id integer NOT NULL,
    agency_id uuid NOT NULL,
    subscription_id integer NOT NULL,
    interview_total integer,
    interview_used integer NOT NULL,
    interview_remaining integer,
    is_interview_unlimited boolean NOT NULL,
    resume_total integer,
    resume_used integer NOT NULL,
    resume_remaining integer,
    is_resume_unlimited boolean NOT NULL,
    job_post_total integer,
    job_post_used integer NOT NULL,
    job_post_remaining integer,
    is_job_post_unlimited boolean NOT NULL,
    user_seat_total integer,
    user_seat_used integer NOT NULL,
    user_seat_remaining integer,
    is_user_seat_unlimited boolean NOT NULL,
    last_reset_date timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.wallets OWNER TO postgres;

--
-- Name: wallets_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.wallets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wallets_id_seq OWNER TO postgres;

--
-- Name: wallets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.wallets_id_seq OWNED BY public.wallets.id;


--
-- Name: webhook_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.webhook_events (
    id integer NOT NULL,
    event_id character varying(255) NOT NULL,
    event_type character varying(100) NOT NULL,
    payment_id character varying(255),
    raw_payload json,
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.webhook_events OWNER TO postgres;

--
-- Name: webhook_events_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.webhook_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.webhook_events_id_seq OWNER TO postgres;

--
-- Name: webhook_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.webhook_events_id_seq OWNED BY public.webhook_events.id;


--
-- Name: agency_discounts id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agency_discounts ALTER COLUMN id SET DEFAULT nextval('public.agency_discounts_id_seq'::regclass);


--
-- Name: enterprise_leads id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.enterprise_leads ALTER COLUMN id SET DEFAULT nextval('public.enterprise_leads_id_seq'::regclass);


--
-- Name: interview_share_access_logs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_access_logs ALTER COLUMN id SET DEFAULT nextval('public.interview_share_access_logs_id_seq'::regclass);


--
-- Name: interview_share_links id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_links ALTER COLUMN id SET DEFAULT nextval('public.interview_share_links_id_seq'::regclass);


--
-- Name: plan_limits id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.plan_limits ALTER COLUMN id SET DEFAULT nextval('public.plan_limits_id_seq'::regclass);


--
-- Name: recording_retry_queue id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recording_retry_queue ALTER COLUMN id SET DEFAULT nextval('public.recording_retry_queue_id_seq'::regclass);


--
-- Name: subscription_activity_logs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscription_activity_logs ALTER COLUMN id SET DEFAULT nextval('public.subscription_activity_logs_id_seq'::regclass);


--
-- Name: subscriptions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscriptions ALTER COLUMN id SET DEFAULT nextval('public.subscriptions_id_seq'::regclass);


--
-- Name: usage_logs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs ALTER COLUMN id SET DEFAULT nextval('public.usage_logs_id_seq'::regclass);


--
-- Name: usage_tracking id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_tracking ALTER COLUMN id SET DEFAULT nextval('public.usage_tracking_id_seq'::regclass);


--
-- Name: wallets id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets ALTER COLUMN id SET DEFAULT nextval('public.wallets_id_seq'::regclass);


--
-- Name: webhook_events id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.webhook_events ALTER COLUMN id SET DEFAULT nextval('public.webhook_events_id_seq'::regclass);


--
-- Name: agencies agencies_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agencies
    ADD CONSTRAINT agencies_pkey PRIMARY KEY (id);


--
-- Name: agencies agencies_slug_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agencies
    ADD CONSTRAINT agencies_slug_key UNIQUE (slug);


--
-- Name: agencies agencies_uuid_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agencies
    ADD CONSTRAINT agencies_uuid_unique UNIQUE (id);


--
-- Name: agency_discounts agency_discounts_agency_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agency_discounts
    ADD CONSTRAINT agency_discounts_agency_id_key UNIQUE (agency_id);


--
-- Name: agency_discounts agency_discounts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agency_discounts
    ADD CONSTRAINT agency_discounts_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: allowed_users allowed_users_email_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.allowed_users
    ADD CONSTRAINT allowed_users_email_key UNIQUE (email);


--
-- Name: allowed_users allowed_users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.allowed_users
    ADD CONSTRAINT allowed_users_pkey PRIMARY KEY (id);


--
-- Name: ats_export_retries ats_export_retries_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ats_export_retries
    ADD CONSTRAINT ats_export_retries_pkey PRIMARY KEY (id);


--
-- Name: ats_exports ats_exports_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ats_exports
    ADD CONSTRAINT ats_exports_pkey PRIMARY KEY (id);


--
-- Name: audit_events audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (id);


--
-- Name: automation_jobs automation_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_jobs
    ADD CONSTRAINT automation_jobs_pkey PRIMARY KEY (id);


--
-- Name: booking_links booking_links_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_pkey PRIMARY KEY (id);


--
-- Name: booking_links booking_links_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_token_key UNIQUE (token);


--
-- Name: candidate_lifecycle_events candidate_lifecycle_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT candidate_lifecycle_events_pkey PRIMARY KEY (id);


--
-- Name: candidate_selection_sessions candidate_selection_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_selection_sessions
    ADD CONSTRAINT candidate_selection_sessions_pkey PRIMARY KEY (id);


--
-- Name: candidates candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_pkey PRIMARY KEY (id);


--
-- Name: candidates candidates_uuid_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_uuid_unique UNIQUE (id);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: embedding_version_registry embedding_version_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.embedding_version_registry
    ADD CONSTRAINT embedding_version_registry_pkey PRIMARY KEY (id);


--
-- Name: enterprise_leads enterprise_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.enterprise_leads
    ADD CONSTRAINT enterprise_leads_pkey PRIMARY KEY (id);


--
-- Name: feed_access_logs feed_access_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_access_logs
    ADD CONSTRAINT feed_access_logs_pkey PRIMARY KEY (id);


--
-- Name: email_templates idx_16989_ix_email_templates_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.email_templates
    ADD CONSTRAINT idx_16989_ix_email_templates_id PRIMARY KEY (id);


--
-- Name: activity_logs idx_17009_ix_activity_logs_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT idx_17009_ix_activity_logs_id PRIMARY KEY (id);


--
-- Name: email_communications idx_17022_ix_email_communications_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.email_communications
    ADD CONSTRAINT idx_17022_ix_email_communications_id PRIMARY KEY (id);


--
-- Name: analytics_widgets idx_17028_ix_analytics_widgets_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.analytics_widgets
    ADD CONSTRAINT idx_17028_ix_analytics_widgets_id PRIMARY KEY (id);


--
-- Name: user_dashboard_preferences idx_17034_ix_user_dashboard_preferences_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_dashboard_preferences
    ADD CONSTRAINT idx_17034_ix_user_dashboard_preferences_id PRIMARY KEY (id);


--
-- Name: wallet_transactions idx_17040_ix_wallet_transactions_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallet_transactions
    ADD CONSTRAINT idx_17040_ix_wallet_transactions_id PRIMARY KEY (id);


--
-- Name: inbound_email_attachments inbound_email_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_attachments
    ADD CONSTRAINT inbound_email_attachments_pkey PRIMARY KEY (id);


--
-- Name: inbound_email_replies inbound_email_replies_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_replies
    ADD CONSTRAINT inbound_email_replies_pkey PRIMARY KEY (id);


--
-- Name: interview_evaluations interview_evaluations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_evaluations
    ADD CONSTRAINT interview_evaluations_pkey PRIMARY KEY (id);


--
-- Name: interview_sessions interview_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_sessions
    ADD CONSTRAINT interview_sessions_pkey PRIMARY KEY (id);


--
-- Name: interview_sessions interview_sessions_session_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_sessions
    ADD CONSTRAINT interview_sessions_session_token_key UNIQUE (session_token);


--
-- Name: interview_share_access_logs interview_share_access_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_access_logs
    ADD CONSTRAINT interview_share_access_logs_pkey PRIMARY KEY (id);


--
-- Name: interview_share_links interview_share_links_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_links
    ADD CONSTRAINT interview_share_links_pkey PRIMARY KEY (id);


--
-- Name: interview_share_links interview_share_links_share_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_links
    ADD CONSTRAINT interview_share_links_share_token_key UNIQUE (share_token);


--
-- Name: interview_slots interview_slots_agency_id_slot_date_slot_time_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_slots
    ADD CONSTRAINT interview_slots_agency_id_slot_date_slot_time_key UNIQUE (agency_id, slot_date, slot_time);


--
-- Name: interview_slots interview_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_slots
    ADD CONSTRAINT interview_slots_pkey PRIMARY KEY (id);


--
-- Name: interviews interviews_async_token_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_async_token_unique UNIQUE (async_token);


--
-- Name: interviews interviews_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_pkey PRIMARY KEY (id);


--
-- Name: job_applications job_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_applications
    ADD CONSTRAINT job_applications_pkey PRIMARY KEY (id);


--
-- Name: job_descriptions job_descriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_descriptions
    ADD CONSTRAINT job_descriptions_pkey PRIMARY KEY (id);


--
-- Name: job_descriptions job_descriptions_uuid_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_descriptions
    ADD CONSTRAINT job_descriptions_uuid_unique UNIQUE (id);


--
-- Name: job_distribution_logs job_distribution_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_distribution_logs
    ADD CONSTRAINT job_distribution_logs_pkey PRIMARY KEY (id);


--
-- Name: job_intakes job_intakes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_intakes
    ADD CONSTRAINT job_intakes_pkey PRIMARY KEY (id);


--
-- Name: job_portal_mapping job_portal_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_portal_mapping
    ADD CONSTRAINT job_portal_mapping_pkey PRIMARY KEY (id);


--
-- Name: job_portals job_portals_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_portals
    ADD CONSTRAINT job_portals_pkey PRIMARY KEY (id);


--
-- Name: notification_events notification_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_pkey PRIMARY KEY (id);


--
-- Name: notification_workflow_tokens notification_workflow_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_workflow_tokens
    ADD CONSTRAINT notification_workflow_tokens_pkey PRIMARY KEY (id);


--
-- Name: orchestration_events orchestration_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_events
    ADD CONSTRAINT orchestration_events_pkey PRIMARY KEY (id);


--
-- Name: orchestration_sessions orchestration_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_sessions
    ADD CONSTRAINT orchestration_sessions_pkey PRIMARY KEY (id);


--
-- Name: orchestration_sessions orchestration_sessions_session_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_sessions
    ADD CONSTRAINT orchestration_sessions_session_token_key UNIQUE (session_token);


--
-- Name: otps otps_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.otps
    ADD CONSTRAINT otps_pkey PRIMARY KEY (id);


--
-- Name: outreach_events outreach_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT outreach_events_pkey PRIMARY KEY (id);


--
-- Name: candidate_feedback pk_candidate_feedback; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT pk_candidate_feedback PRIMARY KEY (id);


--
-- Name: plan_limits plan_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.plan_limits
    ADD CONSTRAINT plan_limits_pkey PRIMARY KEY (id);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);


--
-- Name: ranking_explanations ranking_explanations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_explanations
    ADD CONSTRAINT ranking_explanations_pkey PRIMARY KEY (id);


--
-- Name: ranking_runs ranking_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_runs
    ADD CONSTRAINT ranking_runs_pkey PRIMARY KEY (id);


--
-- Name: recording_retry_queue recording_retry_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recording_retry_queue
    ADD CONSTRAINT recording_retry_queue_pkey PRIMARY KEY (id);


--
-- Name: recruiter_experience_preferences recruiter_experience_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_experience_preferences
    ADD CONSTRAINT recruiter_experience_preferences_pkey PRIMARY KEY (id);


--
-- Name: recruiter_notes recruiter_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_notes
    ADD CONSTRAINT recruiter_notes_pkey PRIMARY KEY (id);


--
-- Name: recruiter_role_preferences recruiter_role_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_role_preferences
    ADD CONSTRAINT recruiter_role_preferences_pkey PRIMARY KEY (id);


--
-- Name: recruiter_skill_preferences recruiter_skill_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_skill_preferences
    ADD CONSTRAINT recruiter_skill_preferences_pkey PRIMARY KEY (id);


--
-- Name: recruiter_tasks recruiter_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_tasks
    ADD CONSTRAINT recruiter_tasks_pkey PRIMARY KEY (id);


--
-- Name: scoring_profiles scoring_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.scoring_profiles
    ADD CONSTRAINT scoring_profiles_pkey PRIMARY KEY (id);


--
-- Name: slack_installations slack_installations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_installations
    ADD CONSTRAINT slack_installations_pkey PRIMARY KEY (id);


--
-- Name: slack_users slack_users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_users
    ADD CONSTRAINT slack_users_pkey PRIMARY KEY (id);


--
-- Name: subscription_activity_logs subscription_activity_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscription_activity_logs
    ADD CONSTRAINT subscription_activity_logs_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: ats_exports uq_ats_exports_job_candidate_provider; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ats_exports
    ADD CONSTRAINT uq_ats_exports_job_candidate_provider UNIQUE (job_id, candidate_id, provider);


--
-- Name: automation_jobs uq_automation_jobs_automation_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_jobs
    ADD CONSTRAINT uq_automation_jobs_automation_key UNIQUE (automation_key);


--
-- Name: candidate_feedback uq_candidate_feedback_job_candidate; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT uq_candidate_feedback_job_candidate UNIQUE (job_id, candidate_id);


--
-- Name: interview_evaluations uq_interview_evaluations_job_candidate_stage; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_evaluations
    ADD CONSTRAINT uq_interview_evaluations_job_candidate_stage UNIQUE (job_id, candidate_id, stage_name);


--
-- Name: job_intakes uq_job_intakes_job; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_intakes
    ADD CONSTRAINT uq_job_intakes_job UNIQUE (job_id);


--
-- Name: job_portal_mapping uq_job_portal_mapping_job_portal; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_portal_mapping
    ADD CONSTRAINT uq_job_portal_mapping_job_portal UNIQUE (job_id, portal_name);


--
-- Name: job_portals uq_job_portals_portal_name; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_portals
    ADD CONSTRAINT uq_job_portals_portal_name UNIQUE (portal_name);


--
-- Name: notification_events uq_notification_events_notification_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT uq_notification_events_notification_key UNIQUE (notification_key);


--
-- Name: plan_limits uq_plan_limits_plan_feature; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.plan_limits
    ADD CONSTRAINT uq_plan_limits_plan_feature UNIQUE (plan_id, feature_name);


--
-- Name: ranking_explanations uq_ranking_explanations_job_candidate; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_explanations
    ADD CONSTRAINT uq_ranking_explanations_job_candidate UNIQUE (job_id, candidate_id);


--
-- Name: scoring_profiles uq_scoring_profiles_job; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.scoring_profiles
    ADD CONSTRAINT uq_scoring_profiles_job UNIQUE (job_id);


--
-- Name: slack_installations uq_slack_installations_team_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_installations
    ADD CONSTRAINT uq_slack_installations_team_id UNIQUE (team_id);


--
-- Name: slack_users uq_slack_users_installation_user; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_users
    ADD CONSTRAINT uq_slack_users_installation_user UNIQUE (slack_installation_id, slack_user_id);


--
-- Name: usage_tracking uq_usage_tracking_user_feature; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_tracking
    ADD CONSTRAINT uq_usage_tracking_user_feature UNIQUE (user_id, feature_name);


--
-- Name: wallets uq_wallets_agency_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets
    ADD CONSTRAINT uq_wallets_agency_id UNIQUE (agency_id);


--
-- Name: wallets uq_wallets_subscription_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets
    ADD CONSTRAINT uq_wallets_subscription_id UNIQUE (subscription_id);


--
-- Name: webhook_events uq_webhook_events_event_id; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT uq_webhook_events_event_id UNIQUE (event_id);


--
-- Name: usage_logs usage_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs
    ADD CONSTRAINT usage_logs_pkey PRIMARY KEY (id);


--
-- Name: usage_tracking usage_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_tracking
    ADD CONSTRAINT usage_tracking_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_uuid_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_uuid_unique UNIQUE (id);


--
-- Name: wallets wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets
    ADD CONSTRAINT wallets_pkey PRIMARY KEY (id);


--
-- Name: webhook_events webhook_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT webhook_events_pkey PRIMARY KEY (id);


--
-- Name: idx_16975_ix_users_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX idx_16975_ix_users_email ON public.users USING btree (email);


--
-- Name: idx_16982_ix_job_descriptions_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX idx_16982_ix_job_descriptions_job_id ON public.job_descriptions USING btree (job_id);


--
-- Name: idx_17001_ix_candidates_candidate_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX idx_17001_ix_candidates_candidate_id ON public.candidates USING btree (candidate_id);


--
-- Name: idx_17001_ix_candidates_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_17001_ix_candidates_email ON public.candidates USING btree (email);


--
-- Name: idx_17028_ix_analytics_widgets_metric_key; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_17028_ix_analytics_widgets_metric_key ON public.analytics_widgets USING btree (metric_key);


--
-- Name: idx_17034_ix_user_dashboard_preferences_widget_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_17034_ix_user_dashboard_preferences_widget_id ON public.user_dashboard_preferences USING btree (widget_id);


--
-- Name: idx_candidate_lifecycle_agency; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidate_lifecycle_agency ON public.candidate_lifecycle_events USING btree (agency_id);


--
-- Name: idx_candidate_lifecycle_candidate; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidate_lifecycle_candidate ON public.candidate_lifecycle_events USING btree (candidate_id);


--
-- Name: idx_candidate_lifecycle_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidate_lifecycle_created_at ON public.candidate_lifecycle_events USING btree (created_at);


--
-- Name: idx_candidate_lifecycle_job; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidate_lifecycle_job ON public.candidate_lifecycle_events USING btree (job_id);


--
-- Name: idx_candidates_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_agency_id ON public.candidates USING btree (agency_id);


--
-- Name: idx_candidates_agency_id_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_agency_id_job_id ON public.candidates USING btree (agency_id, job_id);


--
-- Name: idx_candidates_agency_id_stage; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_agency_id_stage ON public.candidates USING btree (agency_id, stage);


--
-- Name: idx_candidates_assigned_to_user_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_assigned_to_user_id ON public.candidates USING btree (assigned_to_user_id);


--
-- Name: idx_candidates_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_created_at ON public.candidates USING btree (created_at);


--
-- Name: idx_candidates_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_job_id ON public.candidates USING btree (job_id);


--
-- Name: idx_candidates_stage; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_candidates_stage ON public.candidates USING btree (stage);


--
-- Name: idx_enterprise_leads_agency_id_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_enterprise_leads_agency_id_created_at ON public.enterprise_leads USING btree (agency_id, created_at);


--
-- Name: idx_enterprise_leads_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_enterprise_leads_email ON public.enterprise_leads USING btree (email);


--
-- Name: idx_feed_access_logs_portal_accessed_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_feed_access_logs_portal_accessed_at ON public.feed_access_logs USING btree (portal_name, accessed_at);


--
-- Name: idx_interview_sessions_last_activity; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_sessions_last_activity ON public.interview_sessions USING btree (last_activity_at);


--
-- Name: idx_interview_sessions_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_sessions_status ON public.interview_sessions USING btree (status);


--
-- Name: idx_interview_share_access_logs_share_token_accessed_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_share_access_logs_share_token_accessed_at ON public.interview_share_access_logs USING btree (share_token, accessed_at);


--
-- Name: idx_interview_share_links_interview_id_is_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_share_links_interview_id_is_active ON public.interview_share_links USING btree (interview_id, is_active);


--
-- Name: idx_interview_slots_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_slots_date ON public.interview_slots USING btree (slot_date);


--
-- Name: idx_interview_slots_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interview_slots_time ON public.interview_slots USING btree (slot_time);


--
-- Name: idx_interviews_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_agency_id ON public.interviews USING btree (agency_id);


--
-- Name: idx_interviews_agency_id_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_agency_id_status ON public.interviews USING btree (agency_id, status);


--
-- Name: idx_interviews_candidate_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_candidate_id ON public.interviews USING btree (candidate_id);


--
-- Name: idx_interviews_candidate_id_scheduled_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_candidate_id_scheduled_at ON public.interviews USING btree (candidate_id, scheduled_at);


--
-- Name: idx_interviews_scheduled_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_scheduled_at ON public.interviews USING btree (scheduled_at);


--
-- Name: idx_interviews_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_interviews_status ON public.interviews USING btree (status);


--
-- Name: idx_job_applications_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_applications_email ON public.job_applications USING btree (email);


--
-- Name: idx_job_applications_job_id_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_applications_job_id_created_at ON public.job_applications USING btree (job_id, created_at);


--
-- Name: idx_job_descriptions_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_descriptions_agency_id ON public.job_descriptions USING btree (agency_id);


--
-- Name: idx_job_descriptions_agency_id_is_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_descriptions_agency_id_is_active ON public.job_descriptions USING btree (agency_id, is_active);


--
-- Name: idx_job_descriptions_created_by; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_descriptions_created_by ON public.job_descriptions USING btree (created_by);


--
-- Name: idx_job_descriptions_is_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_descriptions_is_active ON public.job_descriptions USING btree (is_active);


--
-- Name: idx_job_distribution_logs_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_distribution_logs_job_id ON public.job_distribution_logs USING btree (job_id);


--
-- Name: idx_job_distribution_logs_portal_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_distribution_logs_portal_status ON public.job_distribution_logs USING btree (portal_name, status);


--
-- Name: idx_job_distribution_logs_posted_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_distribution_logs_posted_at ON public.job_distribution_logs USING btree (posted_at);


--
-- Name: idx_job_portal_mapping_portal_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_portal_mapping_portal_name ON public.job_portal_mapping USING btree (portal_name);


--
-- Name: idx_job_portals_enabled; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_job_portals_enabled ON public.job_portals USING btree (is_enabled);


--
-- Name: idx_plan_limits_plan_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_plan_limits_plan_id ON public.plan_limits USING btree (plan_id);


--
-- Name: idx_plans_name_duration; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_plans_name_duration ON public.plans USING btree (name, duration);


--
-- Name: idx_subscriptions_agency_id_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_subscriptions_agency_id_status ON public.subscriptions USING btree (agency_id, status);


--
-- Name: idx_subscriptions_expires_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_subscriptions_expires_at ON public.subscriptions USING btree (expires_at);


--
-- Name: idx_subscriptions_user_id_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_subscriptions_user_id_created_at ON public.subscriptions USING btree (user_id, created_at);


--
-- Name: idx_usage_logs_agency_id_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_usage_logs_agency_id_created_at ON public.usage_logs USING btree (agency_id, created_at);


--
-- Name: idx_usage_logs_feature_name_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_usage_logs_feature_name_created_at ON public.usage_logs USING btree (feature_name, created_at);


--
-- Name: idx_usage_tracking_user_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_usage_tracking_user_id ON public.usage_tracking USING btree (user_id);


--
-- Name: idx_webhook_events_event_type_processed_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_webhook_events_event_type_processed_at ON public.webhook_events USING btree (event_type, processed_at);


--
-- Name: idx_webhook_events_payment_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_webhook_events_payment_id ON public.webhook_events USING btree (payment_id);


--
-- Name: ix_agency_discounts_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_agency_discounts_id ON public.agency_discounts USING btree (id);


--
-- Name: ix_enterprise_leads_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enterprise_leads_agency_id ON public.enterprise_leads USING btree (agency_id);


--
-- Name: ix_enterprise_leads_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enterprise_leads_email ON public.enterprise_leads USING btree (email);


--
-- Name: ix_enterprise_leads_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enterprise_leads_id ON public.enterprise_leads USING btree (id);


--
-- Name: ix_job_applications_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_job_applications_email ON public.job_applications USING btree (email);


--
-- Name: ix_job_applications_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_job_applications_id ON public.job_applications USING btree (id);


--
-- Name: ix_notification_workflow_tokens_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_agency_id ON public.notification_workflow_tokens USING btree (agency_id);


--
-- Name: ix_notification_workflow_tokens_candidate_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_candidate_id ON public.notification_workflow_tokens USING btree (candidate_id);


--
-- Name: ix_notification_workflow_tokens_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_id ON public.notification_workflow_tokens USING btree (id);


--
-- Name: ix_notification_workflow_tokens_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_job_id ON public.notification_workflow_tokens USING btree (job_id);


--
-- Name: ix_notification_workflow_tokens_token; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_notification_workflow_tokens_token ON public.notification_workflow_tokens USING btree (token);


--
-- Name: ix_notification_workflow_tokens_token_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_token_type ON public.notification_workflow_tokens USING btree (token_type);


--
-- Name: ix_notification_workflow_tokens_user_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_notification_workflow_tokens_user_id ON public.notification_workflow_tokens USING btree (user_id);


--
-- Name: ix_otps_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_otps_email ON public.otps USING btree (email);


--
-- Name: ix_otps_email_expires; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_otps_email_expires ON public.otps USING btree (email, expires_at) WHERE (used = false);


--
-- Name: ix_plan_limits_feature_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_plan_limits_feature_name ON public.plan_limits USING btree (feature_name);


--
-- Name: ix_plan_limits_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_plan_limits_id ON public.plan_limits USING btree (id);


--
-- Name: ix_plans_duration; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_plans_duration ON public.plans USING btree (duration);


--
-- Name: ix_plans_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_plans_id ON public.plans USING btree (id);


--
-- Name: ix_plans_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_plans_name ON public.plans USING btree (name);


--
-- Name: ix_usage_logs_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_agency_id ON public.usage_logs USING btree (agency_id);


--
-- Name: ix_usage_logs_feature_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_feature_name ON public.usage_logs USING btree (feature_name);


--
-- Name: ix_usage_logs_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_id ON public.usage_logs USING btree (id);


--
-- Name: ix_usage_logs_subscription_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_subscription_id ON public.usage_logs USING btree (subscription_id);


--
-- Name: ix_usage_logs_user_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_user_id ON public.usage_logs USING btree (user_id);


--
-- Name: ix_usage_logs_wallet_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_logs_wallet_id ON public.usage_logs USING btree (wallet_id);


--
-- Name: ix_usage_tracking_feature_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_tracking_feature_name ON public.usage_tracking USING btree (feature_name);


--
-- Name: ix_usage_tracking_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_usage_tracking_id ON public.usage_tracking USING btree (id);


--
-- Name: ix_wallets_agency_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_wallets_agency_id ON public.wallets USING btree (agency_id);


--
-- Name: ix_wallets_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_wallets_id ON public.wallets USING btree (id);


--
-- Name: ix_wallets_last_reset_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_wallets_last_reset_date ON public.wallets USING btree (last_reset_date);


--
-- Name: ix_wallets_subscription_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_wallets_subscription_id ON public.wallets USING btree (subscription_id);


--
-- Name: ix_webhook_events_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_webhook_events_id ON public.webhook_events USING btree (id);


--
-- Name: uq_subscriptions_one_active_per_agency; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_subscriptions_one_active_per_agency ON public.subscriptions USING btree (agency_id) WHERE ((status)::text = 'active'::text);


--
-- Name: uq_wallet_transactions_razorpay_payment_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_wallet_transactions_razorpay_payment_id ON public.wallet_transactions USING btree (razorpay_payment_id);


--
-- Name: activity_logs activity_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT activity_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: agency_discounts agency_discounts_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agency_discounts
    ADD CONSTRAINT agency_discounts_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: agency_discounts agency_discounts_set_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agency_discounts
    ADD CONSTRAINT agency_discounts_set_by_user_id_fkey FOREIGN KEY (set_by_user_id) REFERENCES public.users(id);


--
-- Name: ats_export_retries ats_export_retries_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ats_export_retries
    ADD CONSTRAINT ats_export_retries_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: ats_exports ats_exports_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ats_exports
    ADD CONSTRAINT ats_exports_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: audit_events audit_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: audit_events audit_events_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: audit_events audit_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: automation_jobs automation_jobs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.automation_jobs
    ADD CONSTRAINT automation_jobs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: booking_links booking_links_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: booking_links booking_links_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id);


--
-- Name: booking_links booking_links_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: booking_links booking_links_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.booking_links
    ADD CONSTRAINT booking_links_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: candidate_selection_sessions candidate_selection_sessions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_selection_sessions
    ADD CONSTRAINT candidate_selection_sessions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: candidates candidates_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: candidates candidates_assigned_to_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_assigned_to_user_id_fkey FOREIGN KEY (assigned_to_user_id) REFERENCES public.users(id);


--
-- Name: candidates candidates_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: candidates candidates_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: clients clients_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: email_communications email_communications_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.email_communications
    ADD CONSTRAINT email_communications_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id);


--
-- Name: enterprise_leads enterprise_leads_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.enterprise_leads
    ADD CONSTRAINT enterprise_leads_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: activity_logs fk_activity_logs_agency; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT fk_activity_logs_agency FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: candidate_feedback fk_candidate_feedback_agency; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT fk_candidate_feedback_agency FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: candidate_feedback fk_candidate_feedback_job; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT fk_candidate_feedback_job FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: candidate_feedback fk_candidate_feedback_recruiter; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT fk_candidate_feedback_recruiter FOREIGN KEY (recruiter_id) REFERENCES public.users(id);


--
-- Name: candidate_feedback fk_candidate_feedback_session; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT fk_candidate_feedback_session FOREIGN KEY (session_id) REFERENCES public.candidate_selection_sessions(id);


--
-- Name: candidate_feedback fk_candidate_feedback_slack_installation; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_feedback
    ADD CONSTRAINT fk_candidate_feedback_slack_installation FOREIGN KEY (slack_installation_id) REFERENCES public.slack_installations(id);


--
-- Name: candidate_lifecycle_events fk_candidate_lifecycle_actor; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT fk_candidate_lifecycle_actor FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: candidate_lifecycle_events fk_candidate_lifecycle_agency; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT fk_candidate_lifecycle_agency FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: candidate_lifecycle_events fk_candidate_lifecycle_candidate; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT fk_candidate_lifecycle_candidate FOREIGN KEY (candidate_id) REFERENCES public.candidates(id);


--
-- Name: candidate_lifecycle_events fk_candidate_lifecycle_job; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT fk_candidate_lifecycle_job FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: candidate_lifecycle_events fk_candidate_lifecycle_slack; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.candidate_lifecycle_events
    ADD CONSTRAINT fk_candidate_lifecycle_slack FOREIGN KEY (slack_installation_id) REFERENCES public.slack_installations(id);


--
-- Name: interviews fk_interviews_agency; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT fk_interviews_agency FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: job_descriptions fk_job_created_by; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_descriptions
    ADD CONSTRAINT fk_job_created_by FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: subscription_activity_logs fk_subscription_client; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscription_activity_logs
    ADD CONSTRAINT fk_subscription_client FOREIGN KEY (client_id) REFERENCES public.agencies(id) ON DELETE CASCADE;


--
-- Name: subscription_activity_logs fk_subscription_performed_by; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscription_activity_logs
    ADD CONSTRAINT fk_subscription_performed_by FOREIGN KEY (performed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: wallet_transactions fk_wallet_transactions_agency; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallet_transactions
    ADD CONSTRAINT fk_wallet_transactions_agency FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: inbound_email_attachments inbound_email_attachments_reply_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_attachments
    ADD CONSTRAINT inbound_email_attachments_reply_id_fkey FOREIGN KEY (reply_id) REFERENCES public.inbound_email_replies(id);


--
-- Name: inbound_email_replies inbound_email_replies_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_replies
    ADD CONSTRAINT inbound_email_replies_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: inbound_email_replies inbound_email_replies_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_replies
    ADD CONSTRAINT inbound_email_replies_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: inbound_email_replies inbound_email_replies_outreach_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.inbound_email_replies
    ADD CONSTRAINT inbound_email_replies_outreach_event_id_fkey FOREIGN KEY (outreach_event_id) REFERENCES public.outreach_events(id);


--
-- Name: interview_evaluations interview_evaluations_interviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_evaluations
    ADD CONSTRAINT interview_evaluations_interviewer_id_fkey FOREIGN KEY (interviewer_id) REFERENCES public.users(id);


--
-- Name: interview_evaluations interview_evaluations_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_evaluations
    ADD CONSTRAINT interview_evaluations_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: interview_share_links interview_share_links_interview_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_share_links
    ADD CONSTRAINT interview_share_links_interview_id_fkey FOREIGN KEY (interview_id) REFERENCES public.interviews(id) ON DELETE CASCADE;


--
-- Name: interview_slots interview_slots_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interview_slots
    ADD CONSTRAINT interview_slots_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: interviews interviews_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id);


--
-- Name: job_applications job_applications_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_applications
    ADD CONSTRAINT job_applications_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id) ON DELETE CASCADE;


--
-- Name: job_descriptions job_descriptions_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_descriptions
    ADD CONSTRAINT job_descriptions_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: job_intakes job_intakes_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_intakes
    ADD CONSTRAINT job_intakes_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: job_intakes job_intakes_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_intakes
    ADD CONSTRAINT job_intakes_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: job_portal_mapping job_portal_mapping_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.job_portal_mapping
    ADD CONSTRAINT job_portal_mapping_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id) ON DELETE CASCADE;


--
-- Name: notification_events notification_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: notification_events notification_events_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: notification_events notification_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: notification_workflow_tokens notification_workflow_tokens_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_workflow_tokens
    ADD CONSTRAINT notification_workflow_tokens_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: notification_workflow_tokens notification_workflow_tokens_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_workflow_tokens
    ADD CONSTRAINT notification_workflow_tokens_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id);


--
-- Name: notification_workflow_tokens notification_workflow_tokens_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_workflow_tokens
    ADD CONSTRAINT notification_workflow_tokens_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: notification_workflow_tokens notification_workflow_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notification_workflow_tokens
    ADD CONSTRAINT notification_workflow_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: orchestration_events orchestration_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_events
    ADD CONSTRAINT orchestration_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.orchestration_sessions(id);


--
-- Name: orchestration_sessions orchestration_sessions_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_sessions
    ADD CONSTRAINT orchestration_sessions_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: orchestration_sessions orchestration_sessions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orchestration_sessions
    ADD CONSTRAINT orchestration_sessions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: outreach_events outreach_events_agency_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT outreach_events_agency_fk FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: outreach_events outreach_events_job_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outreach_events
    ADD CONSTRAINT outreach_events_job_fk FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: plan_limits plan_limits_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.plan_limits
    ADD CONSTRAINT plan_limits_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: ranking_explanations ranking_explanations_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_explanations
    ADD CONSTRAINT ranking_explanations_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: ranking_explanations ranking_explanations_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_explanations
    ADD CONSTRAINT ranking_explanations_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: ranking_runs ranking_runs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_runs
    ADD CONSTRAINT ranking_runs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: ranking_runs ranking_runs_recruiter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ranking_runs
    ADD CONSTRAINT ranking_runs_recruiter_id_fkey FOREIGN KEY (recruiter_id) REFERENCES public.users(id);


--
-- Name: recruiter_experience_preferences recruiter_experience_preferences_recruiter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_experience_preferences
    ADD CONSTRAINT recruiter_experience_preferences_recruiter_id_fkey FOREIGN KEY (recruiter_id) REFERENCES public.users(id);


--
-- Name: recruiter_role_preferences recruiter_role_preferences_recruiter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_role_preferences
    ADD CONSTRAINT recruiter_role_preferences_recruiter_id_fkey FOREIGN KEY (recruiter_id) REFERENCES public.users(id);


--
-- Name: recruiter_skill_preferences recruiter_skill_preferences_recruiter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recruiter_skill_preferences
    ADD CONSTRAINT recruiter_skill_preferences_recruiter_id_fkey FOREIGN KEY (recruiter_id) REFERENCES public.users(id);


--
-- Name: scoring_profiles scoring_profiles_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.scoring_profiles
    ADD CONSTRAINT scoring_profiles_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.job_descriptions(id);


--
-- Name: slack_installations slack_installations_agency_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_installations
    ADD CONSTRAINT slack_installations_agency_fk FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: slack_installations slack_installations_user_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_installations
    ADD CONSTRAINT slack_installations_user_fk FOREIGN KEY (installed_by_user_id) REFERENCES public.users(id);


--
-- Name: slack_users slack_users_agency_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_users
    ADD CONSTRAINT slack_users_agency_fk FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: slack_users slack_users_installation_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_users
    ADD CONSTRAINT slack_users_installation_fk FOREIGN KEY (slack_installation_id) REFERENCES public.slack_installations(id);


--
-- Name: slack_users slack_users_internal_user_fk; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.slack_users
    ADD CONSTRAINT slack_users_internal_user_fk FOREIGN KEY (internal_user_id) REFERENCES public.users(id);


--
-- Name: subscriptions subscriptions_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: subscriptions subscriptions_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: subscriptions subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: usage_logs usage_logs_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs
    ADD CONSTRAINT usage_logs_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: usage_logs usage_logs_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs
    ADD CONSTRAINT usage_logs_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id);


--
-- Name: usage_logs usage_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs
    ADD CONSTRAINT usage_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: usage_logs usage_logs_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_logs
    ADD CONSTRAINT usage_logs_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.wallets(id);


--
-- Name: usage_tracking usage_tracking_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.usage_tracking
    ADD CONSTRAINT usage_tracking_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: user_dashboard_preferences user_dashboard_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_dashboard_preferences
    ADD CONSTRAINT user_dashboard_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: user_dashboard_preferences user_dashboard_preferences_widget_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_dashboard_preferences
    ADD CONSTRAINT user_dashboard_preferences_widget_id_fkey FOREIGN KEY (widget_id) REFERENCES public.analytics_widgets(id);


--
-- Name: users users_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: wallet_transactions wallet_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallet_transactions
    ADD CONSTRAINT wallet_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: wallets wallets_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets
    ADD CONSTRAINT wallets_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.agencies(id);


--
-- Name: wallets wallets_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.wallets
    ADD CONSTRAINT wallets_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: postgres
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict fW54uIwzCFvJbsULoodHuY5PK8hhMGPKjw1X7W6RshFotZaDCNkErvz2nVCFlGW

