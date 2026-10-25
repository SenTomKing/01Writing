CREATE TABLE wa_users (
	id VARCHAR(36) NOT NULL,
	username VARCHAR(40) NOT NULL,
	password_hash TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (username)
);

CREATE TABLE wa_rate_buckets (
	key VARCHAR(64) NOT NULL,
	count INTEGER NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (key)
);

CREATE INDEX ix_wa_rate_buckets_expires_at ON wa_rate_buckets (expires_at);

CREATE TABLE wa_sessions (
	token_hash VARCHAR(64) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	csrf_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (token_hash),
	FOREIGN KEY(user_id) REFERENCES wa_users (id)
);

CREATE INDEX ix_wa_sessions_expires_at ON wa_sessions (expires_at);

CREATE INDEX ix_wa_sessions_user_id ON wa_sessions (user_id);

CREATE TABLE wa_documents (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	current_version_id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT wa_document_title CHECK (length(trim(title)) BETWEEN 1 AND 200),
	FOREIGN KEY(user_id) REFERENCES wa_users (id)
);

CREATE INDEX wa_documents_owner_updated ON wa_documents (user_id, updated_at, id);

CREATE INDEX ix_wa_documents_user_id ON wa_documents (user_id);

CREATE TABLE wa_imports (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	mapping JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT wa_import_once UNIQUE (user_id, source_hash),
	FOREIGN KEY(user_id) REFERENCES wa_users (id)
);

CREATE TABLE wa_versions (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	content TEXT NOT NULL,
	kind VARCHAR(16) NOT NULL,
	source_task_id VARCHAR(36),
	restored_from_id VARCHAR(36),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT wa_version_document_id UNIQUE (document_id, id),
	CONSTRAINT wa_version_number UNIQUE (document_id, number),
	CONSTRAINT wa_version_content CHECK (number > 0 AND length(trim(content)) > 0),
	CONSTRAINT wa_version_kind CHECK (kind IN ('initial','edit','accepted','restored','imported')),
	CONSTRAINT wa_version_restore_fk FOREIGN KEY(document_id, restored_from_id) REFERENCES wa_versions (document_id, id) DEFERRABLE INITIALLY DEFERRED,
	FOREIGN KEY(document_id) REFERENCES wa_documents (id) DEFERRABLE INITIALLY DEFERRED,
	UNIQUE (source_task_id)
);

CREATE TABLE wa_tasks (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	base_version_id VARCHAR(36) NOT NULL,
	instruction TEXT NOT NULL,
	provider VARCHAR(32) NOT NULL,
	model VARCHAR(100) NOT NULL,
	prompt_version VARCHAR(40) NOT NULL,
	previous_task_id VARCHAR(36),
	feedback JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	idempotency_key VARCHAR(80) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	attempts INTEGER NOT NULL,
	claim_token VARCHAR(36),
	lease_until TIMESTAMP WITH TIME ZONE,
	dispatch_after TIMESTAMP WITH TIME ZONE NOT NULL,
	rejection_reason TEXT,
	error_code VARCHAR(80),
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	decided_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT wa_task_idempotency UNIQUE (user_id, idempotency_key),
	CONSTRAINT wa_task_document_id UNIQUE (document_id, id),
	CONSTRAINT wa_task_status CHECK (status IN ('queued','running','review','accepted','rejected','failed','cancelled')),
	CONSTRAINT wa_task_attempts CHECK (attempts >= 0),
	CONSTRAINT wa_task_base_fk FOREIGN KEY(document_id, base_version_id) REFERENCES wa_versions (document_id, id),
	CONSTRAINT wa_task_previous_fk FOREIGN KEY(document_id, previous_task_id) REFERENCES wa_tasks (document_id, id),
	FOREIGN KEY(user_id) REFERENCES wa_users (id),
	FOREIGN KEY(document_id) REFERENCES wa_documents (id)
);

CREATE INDEX ix_wa_tasks_user_id ON wa_tasks (user_id);

CREATE INDEX wa_task_dispatch ON wa_tasks (status, dispatch_after);

CREATE INDEX ix_wa_tasks_document_id ON wa_tasks (document_id);

CREATE TABLE wa_candidates (
	task_id VARCHAR(36) NOT NULL,
	text TEXT NOT NULL,
	reason TEXT NOT NULL,
	PRIMARY KEY (task_id),
	FOREIGN KEY(task_id) REFERENCES wa_tasks (id)
);

CREATE TABLE wa_attempts (
	id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	claim_token VARCHAR(36) NOT NULL,
	provider VARCHAR(32) NOT NULL,
	model VARCHAR(100) NOT NULL,
	prompt_version VARCHAR(40) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	duration_ms INTEGER,
	usage JSON,
	outcome VARCHAR(20) NOT NULL,
	error_code VARCHAR(80),
	PRIMARY KEY (id),
	CONSTRAINT wa_attempt_number UNIQUE (task_id, number),
	FOREIGN KEY(task_id) REFERENCES wa_tasks (id),
	UNIQUE (claim_token)
);

CREATE INDEX ix_wa_attempts_task_id ON wa_attempts (task_id);

CREATE TABLE wa_events (
	id SERIAL NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	kind VARCHAR(40) NOT NULL,
	data JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES wa_tasks (id)
);

CREATE INDEX ix_wa_events_task_id ON wa_events (task_id);

ALTER TABLE wa_documents ADD CONSTRAINT wa_document_current_fk FOREIGN KEY(id, current_version_id) REFERENCES wa_versions (document_id, id) DEFERRABLE INITIALLY DEFERRED;
