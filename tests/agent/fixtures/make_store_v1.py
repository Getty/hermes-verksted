"""Build store_v1.sql with the schema-1 store of commit c6be848 (not a test).

    git show c6be848:verksted/store.py > /tmp/store_v1.py
    .dev/venv/bin/python tests/agent/fixtures/make_store_v1.py /tmp/store_v1.py > tests/agent/fixtures/store_v1.sql
"""
import importlib.util, sqlite3, sys, tempfile, os
spec = importlib.util.spec_from_file_location("store_v1", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.SCHEMA_VERSION == 1
t = [1000.0]
db = os.path.join(tempfile.mkdtemp(), "v1.db")
st = m.Store.at_path(db, clock=lambda: t[0])
IDENT = {"connection_id": "local", "route_profile": "default", "target_profile": "default",
         "profile_id": "/homes/default", "instance_id": "inst-1"}
PRINC = {"subject": "user-1", "instance_id": "inst-1", "profile_id": "/homes/default"}
def rec(n, folder):
    rid = f"vk-{n:04d}"
    return {"run_id": rid, "parent_session_id": "parent-sid", "parent_session_key": "route-key",
            "parent_profile": "default", "parent_profile_home": "/homes/default", "parent_source": "desktop",
            "backend_platform": "linux", "target_dir": folder, "target_key": folder, "task": f"task {n}",
            "resolved_config": {"version": 1, "workspace": folder, "profile": "default", "toolsets": ["file"], "files": []},
            "config_fingerprint": "sha256:x", "expected_toolsets": ["file"], "request_id": f"{rid}.create",
            "create_params": {"cwd": folder, "cwd_explicit": True, "request_id": f"{rid}.create", "toolsets": ["file", "no_mcp"]},
            "submission_id": f"{rid}.submit.1", "prompt_text": f"task {n}"}
def to_running(rid, sid):
    g = st.claim(rid, "w1", principal=PRINC, lease_s=60)["generation"]
    st.record_step(rid, "w1", g, "create_requested", {"identity": IDENT}, principal=PRINC)
    st.record_step(rid, "w1", g, "session_created", {"identity": IDENT, "session_id": sid}, principal=PRINC)
    st.record_step(rid, "w1", g, "toolsets_checked", {"identity": IDENT, "enabled_toolsets": ["file"], "check": {}}, principal=PRINC)
    st.record_step(rid, "w1", g, "submit_requested", {"identity": IDENT}, principal=PRINC)
    st.record_step(rid, "w1", g, "running", {"identity": IDENT, "admission_id": f"adm-{sid}"}, principal=PRINC)
    return g
for n, folder in ((1, "/work/done"), (2, "/work/busy"), (3, "/work/waiting")):
    st.create_run(rec(n, folder), max_active=4)
g = to_running("vk-0001", "child-1")
st.record_step("vk-0001", "w1", g, "finished", {"identity": IDENT, "outcome": "completed",
               "final_response": "Done.\n<verksted-result>\nsummary: s\noutputs: none\n</verksted-result>"}, principal=PRINC)
to_running("vk-0002", "child-2")
conn = sqlite3.connect(db)
lines = list(conn.iterdump())
assert "schema_version" in "\n".join(lines)
sys.stdout.write("-- A schema-1 Verksted store, built with verksted/store.py of commit c6be848 by\n"
                 "-- tests/agent/fixtures/make_store_v1.py: vk-0001 finished (v1 result_ref), vk-0002 running,\n"
                 "-- vk-0003 waiting_for_gui. Loaded by tests/agent/test_store.py to test the migration.\n")
for line in lines:
    sys.stdout.write(line + "\n")
