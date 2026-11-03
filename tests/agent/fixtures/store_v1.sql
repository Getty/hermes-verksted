-- A schema-1 Verksted store, built with verksted/store.py of commit c6be848 by
-- tests/agent/fixtures/make_store_v1.py: vk-0001 finished (v1 result_ref), vk-0002 running,
-- vk-0003 waiting_for_gui. Loaded by tests/agent/test_store.py to test the migration.
BEGIN TRANSACTION;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('schema_version','1');
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    parent_session_id TEXT NOT NULL,
    parent_session_key TEXT NOT NULL,
    parent_profile TEXT NOT NULL,
    parent_profile_home TEXT NOT NULL,
    parent_source TEXT NOT NULL,
    backend_platform TEXT NOT NULL,
    target_dir TEXT NOT NULL,
    target_key TEXT NOT NULL,
    task TEXT NOT NULL,
    resolved_config TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    expected_toolsets TEXT,
    request_id TEXT NOT NULL UNIQUE,
    create_params TEXT NOT NULL,
    submission_id TEXT NOT NULL UNIQUE,
    prompt_text TEXT NOT NULL,
    state TEXT NOT NULL,
    state_detail TEXT NOT NULL DEFAULT '{}',
    last_known_at REAL,
    claim_owner TEXT,
    claim_generation INTEGER NOT NULL DEFAULT 0,
    claim_expires_at REAL,
    claim_principal TEXT,
    connection_id TEXT,
    route_profile TEXT,
    target_profile TEXT,
    profile_id TEXT,
    instance_id TEXT,
    subject TEXT,
    session_id TEXT,
    admission_id TEXT,
    effective_toolsets TEXT,
    outcome TEXT,
    result_ref TEXT,
    finished_at REAL
);
INSERT INTO "runs" VALUES('vk-0001',1000.0,1000.0,'parent-sid','route-key','default','/homes/default','desktop','linux','/work/done','/work/done','task 1','{"files":[],"profile":"default","toolsets":["file"],"version":1,"workspace":"/work/done"}','sha256:x','["file"]','vk-0001.create','{"cwd":"/work/done","cwd_explicit":true,"request_id":"vk-0001.create","toolsets":["file","no_mcp"]}','vk-0001.submit.1','task 1','finished','{}',1000.0,'w1',1,1060.0,'{"instance_id":"inst-1","profile_id":"/homes/default","subject":"user-1"}','local','default','default','/homes/default','inst-1','user-1','child-1','adm-child-1','["file"]','completed','{"admission_id":"adm-child-1","final_response":"Done.\n<verksted-result>\nsummary: s\noutputs: none\n</verksted-result>","session_id":"child-1","truncated":false}',1000.0);
INSERT INTO "runs" VALUES('vk-0002',1000.0,1000.0,'parent-sid','route-key','default','/homes/default','desktop','linux','/work/busy','/work/busy','task 2','{"files":[],"profile":"default","toolsets":["file"],"version":1,"workspace":"/work/busy"}','sha256:x','["file"]','vk-0002.create','{"cwd":"/work/busy","cwd_explicit":true,"request_id":"vk-0002.create","toolsets":["file","no_mcp"]}','vk-0002.submit.1','task 2','running','{"prompts":[],"receipt_status":"queued"}',1000.0,'w1',1,1060.0,'{"instance_id":"inst-1","profile_id":"/homes/default","subject":"user-1"}','local','default','default','/homes/default','inst-1','user-1','child-2','adm-child-2','["file"]',NULL,NULL,NULL);
INSERT INTO "runs" VALUES('vk-0003',1000.0,1000.0,'parent-sid','route-key','default','/homes/default','desktop','linux','/work/waiting','/work/waiting','task 3','{"files":[],"profile":"default","toolsets":["file"],"version":1,"workspace":"/work/waiting"}','sha256:x','["file"]','vk-0003.create','{"cwd":"/work/waiting","cwd_explicit":true,"request_id":"vk-0003.create","toolsets":["file","no_mcp"]}','vk-0003.submit.1','task 3','waiting_for_gui','{}',NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
CREATE TABLE session_aliases (
    alias TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);
CREATE UNIQUE INDEX runs_one_active_per_folder
    ON runs(target_key) WHERE state IN ('waiting_for_gui', 'create_requested', 'session_created', 'toolsets_checked', 'submit_requested', 'running');
CREATE INDEX runs_session ON runs(session_id);
CREATE INDEX runs_parent ON runs(parent_session_id);
CREATE INDEX runs_updated ON runs(updated_at);
COMMIT;
