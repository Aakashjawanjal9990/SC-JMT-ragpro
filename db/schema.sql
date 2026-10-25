--
-- PostgreSQL database dump
--

-- Dumped from database version 16.15
-- Dumped by pg_dump version 18.0

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
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

SET default_tablespace = '';
SET default_table_access_method = heap;

--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    audit_log_uuid uuid NOT NULL,
    user_uuid uuid,
    session_uuid uuid,
    action text NOT NULL,
    resource_type text,
    resource_id text,
    event_category text NOT NULL,
    severity text NOT NULL,
    status text NOT NULL,
    error_message text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT audit_logs_metadata_check CHECK (((metadata IS NULL) OR (jsonb_typeof(metadata) = 'object'::text)))
);

--
-- Name: categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.categories (
    category_uuid uuid NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

--
-- Name: chunk_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunk_embeddings (
    chunk_embedding_uuid uuid NOT NULL,
    chunk_uuid uuid NOT NULL,
    embedding_model_uuid uuid,
    provider text NOT NULL,
    corpus_resource_name text,
    external_chunk_resource_name text,
    embedding_ref text,
    vector_store text,
    sync_status text NOT NULL,
    last_synced_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    chunk_uuid uuid NOT NULL,
    judgment_uuid uuid NOT NULL,
    source_file_uuid uuid NOT NULL,
    extraction_run_uuid uuid,
    last_seen_ingestion_run_uuid uuid,
    chunk_index integer NOT NULL,
    page_start integer,
    page_end integer,
    content text NOT NULL,
    section text,
    metadata jsonb,
    content_sha256 character(64),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    embedding public.vector(768),
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, content)) STORED,
    CONSTRAINT chunks_check CHECK (((page_start IS NULL) OR (page_end IS NULL) OR (page_end >= page_start))),
    CONSTRAINT chunks_chunk_index_check CHECK ((chunk_index >= 0)),
    CONSTRAINT chunks_content_sha256_check CHECK (((content_sha256 IS NULL) OR (content_sha256 ~ '^[0-9A-Fa-f]{64}$'::text))),
    CONSTRAINT chunks_metadata_check CHECK (((metadata IS NULL) OR (jsonb_typeof(metadata) = 'object'::text))),
    CONSTRAINT chunks_page_end_check CHECK (((page_end IS NULL) OR (page_end > 0))),
    CONSTRAINT chunks_page_start_check CHECK (((page_start IS NULL) OR (page_start > 0)))
);

--
-- Name: citation_checks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citation_checks (
    citation_check_uuid uuid NOT NULL,
    query_run_uuid uuid NOT NULL,
    chunk_uuid uuid,
    judgment_uuid uuid,
    verification_status text NOT NULL,
    citation_text text,
    verification_details jsonb,
    checked_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT citation_checks_verification_details_check CHECK (((verification_details IS NULL) OR (jsonb_typeof(verification_details) = 'object'::text)))
);

--
-- Name: citation_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citation_history (
    citation_history_uuid uuid NOT NULL,
    judgment_uuid uuid NOT NULL,
    citation_uuid uuid,
    event_type text NOT NULL,
    previous_value text,
    new_value text,
    change_reason text,
    changed_by_user_uuid uuid,
    changed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

--
-- Name: citations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citations (
    citation_uuid uuid NOT NULL,
    judgment_uuid uuid NOT NULL,
    citation_text text NOT NULL,
    citation_type text NOT NULL,
    normalized_citation text,
    source_locator text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

--
-- Name: embedding_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embedding_models (
    embedding_model_uuid uuid NOT NULL,
    model_id text NOT NULL,
    provider text NOT NULL,
    version text,
    dimensions integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT embedding_models_dimensions_check CHECK ((dimensions > 0))
);

--
-- Name: extraction_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extraction_runs (
    extraction_run_uuid uuid NOT NULL,
    source_file_uuid uuid NOT NULL,
    ingestion_run_uuid uuid,
    pipeline_version text NOT NULL,
    processing_status text NOT NULL,
    error_message text,
    retry_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT extraction_runs_check CHECK (((retry_count >= 0) AND (max_attempts > 0) AND (retry_count <= max_attempts))),
    CONSTRAINT extraction_runs_check1 CHECK (((finished_at IS NULL) OR (started_at IS NULL) OR (finished_at >= started_at)))
);

--
-- Name: ingestion_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_runs (
    ingestion_run_uuid uuid NOT NULL,
    source_file_uuid uuid NOT NULL,
    pipeline_name text NOT NULL,
    processing_status text NOT NULL,
    error_message text,
    retry_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    idempotency_key text NOT NULL,
    worker_id text,
    started_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT ingestion_runs_check CHECK (((retry_count >= 0) AND (max_attempts > 0) AND (retry_count <= max_attempts))),
    CONSTRAINT ingestion_runs_check1 CHECK (((finished_at IS NULL) OR (started_at IS NULL) OR (finished_at >= started_at)))
);

--
-- Name: judgment_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.judgment_categories (
    judgment_category_uuid uuid NOT NULL,
    judgment_uuid uuid NOT NULL,
    category_uuid uuid NOT NULL,
    confidence numeric,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT judgment_categories_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))))
);

--
-- Name: judgments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.judgments (
    judgment_uuid uuid NOT NULL,
    judgment_id text NOT NULL,
    court text,
    case_number text,
    judgment_date date,
    citation_text text,
    reporter_citations text,
    legal_status text,
    is_primary boolean DEFAULT false NOT NULL,
    overruled_by_judgment_uuid uuid,
    refresh_interval_days integer,
    last_refreshed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT judgments_check CHECK (((overruled_by_judgment_uuid IS NULL) OR (overruled_by_judgment_uuid <> judgment_uuid))),
    CONSTRAINT judgments_refresh_interval_days_check CHECK (((refresh_interval_days IS NULL) OR (refresh_interval_days > 0)))
);

--
-- Name: outbox_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_events (
    outbox_event_uuid uuid NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_uuid uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    delivery_status text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    published_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT outbox_events_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT outbox_events_check CHECK (((published_at IS NULL) OR (published_at >= created_at))),
    CONSTRAINT outbox_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);

--
-- Name: prompt_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompt_templates (
    prompt_template_uuid uuid NOT NULL,
    template_id text NOT NULL,
    stage text NOT NULL,
    system_instruction text,
    user_template text NOT NULL,
    output_format text,
    input_variables jsonb,
    model_name text,
    thinking_mode text,
    token_budget integer,
    template_version integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT prompt_templates_input_variables_check CHECK (((input_variables IS NULL) OR (jsonb_typeof(input_variables) = ANY (ARRAY['object'::text, 'array'::text])))),
    CONSTRAINT prompt_templates_template_version_check CHECK ((template_version > 0)),
    CONSTRAINT prompt_templates_token_budget_check CHECK (((token_budget IS NULL) OR (token_budget > 0)))
);

--
-- Name: query_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.query_runs (
    query_run_uuid uuid NOT NULL,
    user_uuid uuid,
    session_uuid uuid,
    prompt_template_uuid uuid,
    request_type text NOT NULL,
    query_text text NOT NULL,
    response_text text,
    retrieved_chunks jsonb,
    metadata jsonb,
    status text NOT NULL,
    latency_ms integer,
    model_name text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT query_runs_latency_ms_check CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT query_runs_metadata_check CHECK (((metadata IS NULL) OR (jsonb_typeof(metadata) = 'object'::text))),
    CONSTRAINT query_runs_retrieved_chunks_check CHECK (((retrieved_chunks IS NULL) OR (jsonb_typeof(retrieved_chunks) = 'array'::text)))
);

--
-- Name: source_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_files (
    source_file_uuid uuid NOT NULL,
    judgment_uuid uuid NOT NULL,
    version_number text NOT NULL,
    is_current boolean DEFAULT false NOT NULL,
    original_filename text NOT NULL,
    storage_path text NOT NULL,
    doc_encryption_files jsonb,
    page_count integer,
    ingestion_status text NOT NULL,
    pipeline_version text,
    created_by_user_uuid uuid,
    content_sha256 character(64),
    file_size_bytes bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT source_files_content_sha256_check CHECK (((content_sha256 IS NULL) OR (content_sha256 ~ '^[0-9A-Fa-f]{64}$'::text))),
    CONSTRAINT source_files_doc_encryption_files_check CHECK (((doc_encryption_files IS NULL) OR (jsonb_typeof(doc_encryption_files) = ANY (ARRAY['object'::text, 'array'::text])))),
    CONSTRAINT source_files_file_size_bytes_check CHECK (((file_size_bytes IS NULL) OR (file_size_bytes >= 0))),
    CONSTRAINT source_files_page_count_check CHECK (((page_count IS NULL) OR (page_count >= 0)))
);

--
-- Name: source_pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_pages (
    source_page_uuid uuid NOT NULL,
    source_file_uuid uuid NOT NULL,
    page_number integer NOT NULL,
    extraction_method text NOT NULL,
    content text,
    quality_score numeric,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT source_pages_page_number_check CHECK ((page_number > 0)),
    CONSTRAINT source_pages_quality_score_check CHECK (((quality_score IS NULL) OR ((quality_score >= (0)::numeric) AND (quality_score <= (1)::numeric))))
);

--
-- Name: user_activity_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_activity_logs (
    user_activity_log_uuid uuid NOT NULL,
    user_uuid uuid,
    session_uuid uuid,
    event_type text NOT NULL,
    action text NOT NULL,
    resource_type text,
    resource_id text,
    ip_address inet,
    user_agent text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT user_activity_logs_metadata_check CHECK (((metadata IS NULL) OR (jsonb_typeof(metadata) = 'object'::text)))
);

--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    session_uuid uuid NOT NULL,
    user_uuid uuid NOT NULL,
    session_token_hash text NOT NULL,
    login_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_activity_at timestamp with time zone,
    logout_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    ip_address inet,
    user_agent text,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT user_sessions_check CHECK ((expires_at > login_at)),
    CONSTRAINT user_sessions_check1 CHECK (((logout_at IS NULL) OR (logout_at >= login_at)))
);

--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_uuid uuid NOT NULL,
    username text NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    full_name text,
    role text NOT NULL,
    account_status text NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT users_email_check CHECK ((length(btrim(email)) > 0)),
    CONSTRAINT users_username_check CHECK ((length(btrim(username)) > 0))
);

--
-- Constraints & Keys
--

ALTER TABLE ONLY public.audit_logs ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (audit_log_uuid);
ALTER TABLE ONLY public.categories ADD CONSTRAINT categories_name_key UNIQUE (name);
ALTER TABLE ONLY public.categories ADD CONSTRAINT categories_pkey PRIMARY KEY (category_uuid);
ALTER TABLE ONLY public.categories ADD CONSTRAINT categories_slug_key UNIQUE (slug);
ALTER TABLE ONLY public.chunk_embeddings ADD CONSTRAINT chunk_embeddings_chunk_uuid_embedding_model_uuid_key UNIQUE (chunk_uuid, embedding_model_uuid);
ALTER TABLE ONLY public.chunk_embeddings ADD CONSTRAINT chunk_embeddings_pkey PRIMARY KEY (chunk_embedding_uuid);
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_pkey PRIMARY KEY (chunk_uuid);
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_source_file_uuid_chunk_index_key UNIQUE (source_file_uuid, chunk_index);
ALTER TABLE ONLY public.citation_checks ADD CONSTRAINT citation_checks_pkey PRIMARY KEY (citation_check_uuid);
ALTER TABLE ONLY public.citation_history ADD CONSTRAINT citation_history_pkey PRIMARY KEY (citation_history_uuid);
ALTER TABLE ONLY public.citations ADD CONSTRAINT citations_pkey PRIMARY KEY (citation_uuid);
ALTER TABLE ONLY public.embedding_models ADD CONSTRAINT embedding_models_pkey PRIMARY KEY (embedding_model_uuid);
ALTER TABLE ONLY public.embedding_models ADD CONSTRAINT embedding_models_provider_model_id_version_key UNIQUE (provider, model_id, version);
ALTER TABLE ONLY public.extraction_runs ADD CONSTRAINT extraction_runs_pkey PRIMARY KEY (extraction_run_uuid);
ALTER TABLE ONLY public.ingestion_runs ADD CONSTRAINT ingestion_runs_idempotency_key_key UNIQUE (idempotency_key);
ALTER TABLE ONLY public.ingestion_runs ADD CONSTRAINT ingestion_runs_pkey PRIMARY KEY (ingestion_run_uuid);
ALTER TABLE ONLY public.judgment_categories ADD CONSTRAINT judgment_categories_judgment_uuid_category_uuid_key UNIQUE (judgment_uuid, category_uuid);
ALTER TABLE ONLY public.judgment_categories ADD CONSTRAINT judgment_categories_pkey PRIMARY KEY (judgment_category_uuid);
ALTER TABLE ONLY public.judgments ADD CONSTRAINT judgments_judgment_id_key UNIQUE (judgment_id);
ALTER TABLE ONLY public.judgments ADD CONSTRAINT judgments_pkey PRIMARY KEY (judgment_uuid);
ALTER TABLE ONLY public.outbox_events ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (outbox_event_uuid);
ALTER TABLE ONLY public.prompt_templates ADD CONSTRAINT prompt_templates_pkey PRIMARY KEY (prompt_template_uuid);
ALTER TABLE ONLY public.prompt_templates ADD CONSTRAINT prompt_templates_template_id_stage_template_version_key UNIQUE (template_id, stage, template_version);
ALTER TABLE ONLY public.query_runs ADD CONSTRAINT query_runs_pkey PRIMARY KEY (query_run_uuid);
ALTER TABLE ONLY public.source_files ADD CONSTRAINT source_files_judgment_uuid_version_number_key UNIQUE (judgment_uuid, version_number);
ALTER TABLE ONLY public.source_files ADD CONSTRAINT source_files_pkey PRIMARY KEY (source_file_uuid);
ALTER TABLE ONLY public.source_pages ADD CONSTRAINT source_pages_pkey PRIMARY KEY (source_page_uuid);
ALTER TABLE ONLY public.source_pages ADD CONSTRAINT source_pages_source_file_uuid_page_number_key UNIQUE (source_file_uuid, page_number);
ALTER TABLE ONLY public.user_activity_logs ADD CONSTRAINT user_activity_logs_pkey PRIMARY KEY (user_activity_log_uuid);
ALTER TABLE ONLY public.user_sessions ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (session_uuid);
ALTER TABLE ONLY public.user_sessions ADD CONSTRAINT user_sessions_session_token_hash_key UNIQUE (session_token_hash);
ALTER TABLE ONLY public.users ADD CONSTRAINT users_email_key UNIQUE (email);
ALTER TABLE ONLY public.users ADD CONSTRAINT users_pkey PRIMARY KEY (user_uuid);
ALTER TABLE ONLY public.users ADD CONSTRAINT users_username_key UNIQUE (username);

--
-- Indexes
--

CREATE INDEX audit_logs_category_created_idx ON public.audit_logs USING btree (event_category, created_at DESC);
CREATE INDEX audit_logs_resource_idx ON public.audit_logs USING btree (resource_type, resource_id);
CREATE INDEX audit_logs_severity_created_idx ON public.audit_logs USING btree (severity, created_at DESC);
CREATE INDEX audit_logs_user_created_idx ON public.audit_logs USING btree (user_uuid, created_at DESC);
CREATE INDEX chunk_embeddings_corpus_idx ON public.chunk_embeddings USING btree (corpus_resource_name);
CREATE INDEX chunk_embeddings_external_chunk_idx ON public.chunk_embeddings USING btree (external_chunk_resource_name);
CREATE INDEX chunk_embeddings_model_idx ON public.chunk_embeddings USING btree (embedding_model_uuid);
CREATE INDEX chunk_embeddings_sync_status_idx ON public.chunk_embeddings USING btree (sync_status);
CREATE INDEX chunks_extraction_run_idx ON public.chunks USING btree (extraction_run_uuid);
CREATE INDEX chunks_judgment_active_idx ON public.chunks USING btree (judgment_uuid, is_active);
CREATE INDEX chunks_last_seen_ingestion_idx ON public.chunks USING btree (last_seen_ingestion_run_uuid);
CREATE INDEX chunks_sha256_idx ON public.chunks USING btree (content_sha256);
CREATE INDEX chunks_source_file_idx ON public.chunks USING btree (source_file_uuid);
CREATE INDEX citation_checks_chunk_idx ON public.citation_checks USING btree (chunk_uuid);
CREATE INDEX citation_checks_judgment_idx ON public.citation_checks USING btree (judgment_uuid);
CREATE INDEX citation_checks_query_checked_idx ON public.citation_checks USING btree (query_run_uuid, checked_at DESC);
CREATE INDEX citation_checks_status_idx ON public.citation_checks USING btree (verification_status);
CREATE INDEX citation_history_citation_idx ON public.citation_history USING btree (citation_uuid);
CREATE INDEX citation_history_judgment_changed_idx ON public.citation_history USING btree (judgment_uuid, changed_at DESC);
CREATE INDEX citations_judgment_idx ON public.citations USING btree (judgment_uuid);
CREATE INDEX citations_normalized_idx ON public.citations USING btree (normalized_citation);
CREATE INDEX extraction_runs_ingestion_idx ON public.extraction_runs USING btree (ingestion_run_uuid);
CREATE INDEX extraction_runs_source_created_idx ON public.extraction_runs USING btree (source_file_uuid, created_at DESC);
CREATE INDEX extraction_runs_status_created_idx ON public.extraction_runs USING btree (processing_status, created_at);
CREATE INDEX idx_chunks_embedding_hnsw ON public.chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');
CREATE INDEX idx_chunks_fts ON public.chunks USING gin (search_vector);
CREATE INDEX idx_chunks_metadata_gin ON public.chunks USING gin (metadata);
CREATE INDEX ingestion_runs_active_heartbeat_idx ON public.ingestion_runs USING btree (heartbeat_at) WHERE (finished_at IS NULL);
CREATE INDEX ingestion_runs_source_created_idx ON public.ingestion_runs USING btree (source_file_uuid, created_at DESC);
CREATE INDEX ingestion_runs_status_created_idx ON public.ingestion_runs USING btree (processing_status, created_at);
CREATE INDEX judgment_categories_category_idx ON public.judgment_categories USING btree (category_uuid);
CREATE INDEX outbox_events_aggregate_idx ON public.outbox_events USING btree (aggregate_type, aggregate_uuid);
CREATE INDEX outbox_events_delivery_available_idx ON public.outbox_events USING btree (delivery_status, available_at, created_at);
CREATE INDEX outbox_events_unpublished_idx ON public.outbox_events USING btree (available_at, created_at) WHERE (published_at IS NULL);
CREATE INDEX prompt_templates_active_stage_idx ON public.prompt_templates USING btree (stage, is_active);
CREATE INDEX query_runs_prompt_template_idx ON public.query_runs USING btree (prompt_template_uuid);
CREATE INDEX query_runs_session_created_idx ON public.query_runs USING btree (session_uuid, created_at DESC);
CREATE INDEX query_runs_status_created_idx ON public.query_runs USING btree (status, created_at DESC);
CREATE INDEX query_runs_user_created_idx ON public.query_runs USING btree (user_uuid, created_at DESC);
CREATE INDEX source_files_ingestion_status_idx ON public.source_files USING btree (ingestion_status);
CREATE INDEX source_files_judgment_created_idx ON public.source_files USING btree (judgment_uuid, created_at DESC);
CREATE UNIQUE INDEX source_files_one_current_per_judgment_uq ON public.source_files USING btree (judgment_uuid) WHERE is_current;
CREATE INDEX source_pages_source_idx ON public.source_pages USING btree (source_file_uuid);
CREATE INDEX user_activity_logs_event_created_idx ON public.user_activity_logs USING btree (event_type, created_at DESC);
CREATE INDEX user_activity_logs_session_created_idx ON public.user_activity_logs USING btree (session_uuid, created_at DESC);
CREATE INDEX user_activity_logs_user_created_idx ON public.user_activity_logs USING btree (user_uuid, created_at DESC);
CREATE INDEX user_sessions_status_expiry_idx ON public.user_sessions USING btree (status, expires_at);
CREATE INDEX user_sessions_user_login_idx ON public.user_sessions USING btree (user_uuid, login_at DESC);

--
-- Foreign Key Constraints
--

ALTER TABLE ONLY public.audit_logs ADD CONSTRAINT audit_logs_session_uuid_fkey FOREIGN KEY (session_uuid) REFERENCES public.user_sessions(session_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.audit_logs ADD CONSTRAINT audit_logs_user_uuid_fkey FOREIGN KEY (user_uuid) REFERENCES public.users(user_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.chunk_embeddings ADD CONSTRAINT chunk_embeddings_chunk_uuid_fkey FOREIGN KEY (chunk_uuid) REFERENCES public.chunks(chunk_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.chunk_embeddings ADD CONSTRAINT chunk_embeddings_embedding_model_uuid_fkey FOREIGN KEY (embedding_model_uuid) REFERENCES public.embedding_models(embedding_model_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_extraction_run_uuid_fkey FOREIGN KEY (extraction_run_uuid) REFERENCES public.extraction_runs(extraction_run_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_last_seen_ingestion_run_uuid_fkey FOREIGN KEY (last_seen_ingestion_run_uuid) REFERENCES public.ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.chunks ADD CONSTRAINT chunks_source_file_uuid_fkey FOREIGN KEY (source_file_uuid) REFERENCES public.source_files(source_file_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.citation_checks ADD CONSTRAINT citation_checks_chunk_uuid_fkey FOREIGN KEY (chunk_uuid) REFERENCES public.chunks(chunk_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.citation_checks ADD CONSTRAINT citation_checks_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.citation_checks ADD CONSTRAINT citation_checks_query_run_uuid_fkey FOREIGN KEY (query_run_uuid) REFERENCES public.query_runs(query_run_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.citation_history ADD CONSTRAINT citation_history_changed_by_user_uuid_fkey FOREIGN KEY (changed_by_user_uuid) REFERENCES public.users(user_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.citation_history ADD CONSTRAINT citation_history_citation_uuid_fkey FOREIGN KEY (citation_uuid) REFERENCES public.citations(citation_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.citation_history ADD CONSTRAINT citation_history_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.citations ADD CONSTRAINT citations_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.extraction_runs ADD CONSTRAINT extraction_runs_ingestion_run_uuid_fkey FOREIGN KEY (ingestion_run_uuid) REFERENCES public.ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.extraction_runs ADD CONSTRAINT extraction_runs_source_file_uuid_fkey FOREIGN KEY (source_file_uuid) REFERENCES public.source_files(source_file_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.ingestion_runs ADD CONSTRAINT ingestion_runs_source_file_uuid_fkey FOREIGN KEY (source_file_uuid) REFERENCES public.source_files(source_file_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.judgment_categories ADD CONSTRAINT judgment_categories_category_uuid_fkey FOREIGN KEY (category_uuid) REFERENCES public.categories(category_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.judgment_categories ADD CONSTRAINT judgment_categories_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.judgments ADD CONSTRAINT judgments_overruled_by_judgment_uuid_fkey FOREIGN KEY (overruled_by_judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.query_runs ADD CONSTRAINT query_runs_prompt_template_uuid_fkey FOREIGN KEY (prompt_template_uuid) REFERENCES public.prompt_templates(prompt_template_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.query_runs ADD CONSTRAINT query_runs_session_uuid_fkey FOREIGN KEY (session_uuid) REFERENCES public.user_sessions(session_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.query_runs ADD CONSTRAINT query_runs_user_uuid_fkey FOREIGN KEY (user_uuid) REFERENCES public.users(user_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.source_files ADD CONSTRAINT source_files_created_by_user_uuid_fkey FOREIGN KEY (created_by_user_uuid) REFERENCES public.users(user_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.source_files ADD CONSTRAINT source_files_judgment_uuid_fkey FOREIGN KEY (judgment_uuid) REFERENCES public.judgments(judgment_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.source_pages ADD CONSTRAINT source_pages_source_file_uuid_fkey FOREIGN KEY (source_file_uuid) REFERENCES public.source_files(source_file_uuid) ON DELETE CASCADE;
ALTER TABLE ONLY public.user_activity_logs ADD CONSTRAINT user_activity_logs_session_uuid_fkey FOREIGN KEY (session_uuid) REFERENCES public.user_sessions(session_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.user_activity_logs ADD CONSTRAINT user_activity_logs_user_uuid_fkey FOREIGN KEY (user_uuid) REFERENCES public.users(user_uuid) ON DELETE SET NULL;
ALTER TABLE ONLY public.user_sessions ADD CONSTRAINT user_sessions_user_uuid_fkey FOREIGN KEY (user_uuid) REFERENCES public.users(user_uuid) ON DELETE CASCADE;
