"""Verksted's run and claim store: one SQLite file in the parent profile's plugin-data folder.

Standard library only. The agent plugin imports it as ``hermes_plugins.verksted.store``;
``dashboard/plugin_api.py`` loads the same file by path, because Hermes imports that one as a
standalone module that may run in another process. They share nothing but the database.

Every write runs in one ``BEGIN IMMEDIATE`` transaction on a connection opened for that call, so
a claim grant and the state change it allows are atomic, and a higher claim generation locks out
confirmations of an older one. The store keeps assignments, ids, the claim, the frozen
configuration with its fingerprint, the last known state and a result reference — no history
beyond the earlier turns of a run that ``send`` continued (table ``turns``).

Schema 2 (cards k8, k10) adds the stop request, the turn counter with its archive and the states
``send_waiting`` and ``cancelled``; a schema-1 store is migrated in place, in one transaction.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

SCHEMA_VERSION = 2
PLUGIN_NAME = "verksted"

# A run is "active" while it holds its folder. The unique partial index below makes "one active run
# per folder" atomic; the tool checks it first only to name the run that holds the folder.
ACTIVE_STATES = (
    "waiting_for_gui",   # stored; nothing requested from Hermes yet
    "create_requested",  # the Desktop part is about to call (or called) session.create
    "session_created",   # the child session exists, its tools are not checked yet
    "toolsets_checked",  # the effective toolsets passed the check
    "submit_requested",  # the Desktop part is about to call (or called) prompt.submit
    "running",           # the prompt was admitted (accepted, not done)
    "send_waiting",      # a follow-up turn for the same child session is stored (verksted send)
)
# "cancelled": stopped before the turn's prompt was submitted, so Hermes never saw the task.
FINAL_STATES = ("finished", "refused", "unknown", "cancelled")
STATES = ACTIVE_STATES + FINAL_STATES

# Steps the Desktop part may record, by current state. "refused" means Hermes or Verksted's own check
# refused the start definitively; "unknown" means the outcome is unclear and nothing is retried.
TRANSITIONS: dict[str, frozenset[str]] = {
    "waiting_for_gui": frozenset({"create_requested", "refused"}),
    "create_requested": frozenset({"session_created", "refused", "unknown"}),
    "session_created": frozenset({"toolsets_checked", "refused", "unknown"}),
    "toolsets_checked": frozenset({"submit_requested", "refused", "unknown"}),
    "submit_requested": frozenset({"running", "refused", "unknown", "cancelled"}),
    "running": frozenset({"running", "finished", "unknown"}),
    "send_waiting": frozenset({"submit_requested", "refused"}),
}
# Before the prompt is submitted a stop needs nobody: the run ends ``cancelled`` at once, and the
# Desktop part's next fenced step is refused. From ``submit_requested`` on only Hermes can stop it.
CANCEL_AT_ONCE = ("waiting_for_gui", "create_requested", "session_created", "toolsets_checked", "send_waiting")
# Runs a follow-up turn (``send``) may continue: the child session exists and its last turn ended.
SENDABLE_STATES = ("finished", "cancelled")

# Fields of the Desktop part's connection identity. "subject" comes from the plugin API's host
# authentication, the others from the route descriptor and runtime.describe.
IDENTITY_FIELDS = ("connection_id", "route_profile", "target_profile", "profile_id", "subject")

RESULT_TEXT_LIMIT = 32 * 1024
DETAIL_TEXT_LIMIT = 2000
SUMMARY_LIMIT = 2000
OUTPUTS_LIMIT = 50
PATH_LIMIT = 1024
NOTES_LIMIT = 8

_ACTIVE_SQL = ", ".join(f"'{s}'" for s in ACTIVE_STATES)

# Earlier turns of a run, archived when ``send`` stores the next one; the current turn lives on
# ``runs``, so the claim, start and tracking code reads one row.
_TURNS = """CREATE TABLE IF NOT EXISTS turns (
    run_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    submission_id TEXT NOT NULL UNIQUE,
    task TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    state TEXT NOT NULL,
    state_detail TEXT NOT NULL DEFAULT '{}',
    admission_id TEXT,
    outcome TEXT,
    result_ref TEXT,
    cancel_requested_at REAL,
    finished_at REAL,
    PRIMARY KEY (run_id, turn)
)"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
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
    state_detail TEXT NOT NULL DEFAULT '{{}}',
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
    finished_at REAL,
    cancel_requested_at REAL,
    turn_no INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_per_folder
    ON runs(target_key) WHERE state IN ({_ACTIVE_SQL});
CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS runs_parent ON runs(parent_session_id);
CREATE INDEX IF NOT EXISTS runs_updated ON runs(updated_at);
CREATE TABLE IF NOT EXISTS session_aliases (
    alias TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);
{_TURNS}
"""

# Schema 1 -> 2, in the same transaction as the version bump. The partial index names the active
# states, so it is rebuilt for "send_waiting".
_MIGRATE_1_TO_2 = (
    "ALTER TABLE runs ADD COLUMN cancel_requested_at REAL",
    "ALTER TABLE runs ADD COLUMN turn_no INTEGER NOT NULL DEFAULT 1",
    _TURNS,
    "DROP INDEX IF EXISTS runs_one_active_per_folder",
    f"CREATE UNIQUE INDEX runs_one_active_per_folder ON runs(target_key) WHERE state IN ({_ACTIVE_SQL})",
)

_JSON_COLUMNS = ("resolved_config", "expected_toolsets", "create_params", "state_detail",
                 "claim_principal", "effective_toolsets", "result_ref")


class StoreError(RuntimeError):
    """The store refused an operation; ``code`` is machine-readable."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _clip(text: Any, limit: int) -> tuple[str, bool]:
    text = "" if text is None else str(text)
    return (text, False) if len(text) <= limit else (text[:limit], True)


class Store:
    """One store = one SQLite file. ``connect`` opens a fresh connection per operation."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], *,
                 clock: Callable[[], float] = time.time) -> None:
        self._connect = connect
        self._clock = clock
        self._schema_ready = False

    @classmethod
    def at_path(cls, path: Path | str, **kwargs: Any) -> "Store":
        """A store on an explicit file (tests, tooling)."""
        def connect() -> sqlite3.Connection:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        return cls(connect, **kwargs)

    @classmethod
    def for_active_profile(cls, **kwargs: Any) -> "Store":
        """The store of the profile the calling context runs for: ``<home>/plugin-data/verksted/``.

        ``plugin_db`` resolves ``get_hermes_home()`` on every call, so the tool handler gets the
        parent session's profile and the plugin API the profile its host authentication names.
        """
        from plugins.plugin_storage import plugin_db
        return cls(lambda: plugin_db(PLUGIN_NAME), **kwargs)

    # -- plumbing -----------------------------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        conn = self._connect()
        conn.isolation_level = None  # explicit BEGIN/COMMIT only
        conn.row_factory = sqlite3.Row
        if not self._schema_ready:
            try:
                self._ensure_schema(conn)
            except BaseException:
                conn.close()
                raise
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        # Every tool call opens a fresh Store: a current schema is confirmed by one read, and only
        # a missing or older one takes the write lock. A newer one is refused either way.
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:  # no meta table yet
            row = None
        found = int(row[0]) if row else 0
        if found > SCHEMA_VERSION:
            raise StoreError("schema_too_new", f"store schema {found} is newer than this Verksted ({SCHEMA_VERSION})")
        if found == SCHEMA_VERSION:
            self._schema_ready = True
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            found = int(row[0]) if row else 0
            if found > SCHEMA_VERSION:
                raise StoreError("schema_too_new",
                                 f"store schema {found} is newer than this Verksted ({SCHEMA_VERSION})")
            if found == 0:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
            else:  # found == 1: the only older schema
                for statement in _MIGRATE_1_TO_2:
                    conn.execute(statement)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        self._schema_ready = True

    def _write(self, fn: Callable[[sqlite3.Connection, float], Any]) -> Any:
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn, self._clock())
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result
        finally:
            conn.close()

    def _read(self, fn: Callable[[sqlite3.Connection, float], Any]) -> Any:
        conn = self._open()
        try:
            return fn(conn, self._clock())
        finally:
            conn.close()

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        out = dict(row)
        for key in _JSON_COLUMNS:
            if out.get(key) is not None:
                out[key] = json.loads(out[key])
        return out

    # -- runs ---------------------------------------------------------------------------------

    def create_run(self, run: dict[str, Any], *, max_active: int) -> dict[str, Any]:
        """Insert a new run in ``waiting_for_gui``. Every id and the exact create params and prompt
        text are stored here, before anything can request a start.

        Refuses ``folder_busy`` (another active run holds ``target_key``) and ``too_many_runs``.
        """
        required = ("run_id", "parent_session_id", "parent_session_key", "parent_profile",
                    "parent_profile_home", "parent_source", "backend_platform", "target_dir",
                    "target_key", "task", "resolved_config", "config_fingerprint", "request_id",
                    "create_params", "submission_id", "prompt_text")
        missing = [k for k in required if k not in run]
        if missing:
            raise ValueError(f"create_run: missing {missing}")

        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            busy = conn.execute(
                f"SELECT run_id FROM runs WHERE target_key=? AND state IN ({_ACTIVE_SQL})",
                (run["target_key"],)).fetchone()
            if busy:
                raise StoreError("folder_busy", "another Verksted run is active in this folder",
                                 run_id=busy[0])
            active = conn.execute(f"SELECT COUNT(*) FROM runs WHERE state IN ({_ACTIVE_SQL})").fetchone()[0]
            if active >= max_active:
                raise StoreError("too_many_runs",
                                 f"{active} Verksted runs are active in this profile (limit {max_active})")
            values = {k: run[k] for k in required}
            values["expected_toolsets"] = run.get("expected_toolsets")
            for key in ("resolved_config", "create_params", "expected_toolsets"):
                if values[key] is not None:
                    values[key] = _dumps(values[key])
            values.update(created_at=now, updated_at=now, state="waiting_for_gui", state_detail="{}")
            cols = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            try:
                conn.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", tuple(values.values()))
            except sqlite3.IntegrityError as exc:  # the partial unique index, or an id collision
                raise StoreError("folder_busy", f"run could not be stored: {exc}") from exc
            return self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run["run_id"],)).fetchone())

        return self._write(op)

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return self._read(lambda conn, now: self._row(
            conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()))

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Most recently updated runs first."""
        return self._read(lambda conn, now: [
            self._row(r) for r in conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()])

    def claimable_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Active runs nobody holds a live claim on, oldest first."""
        return self._read(lambda conn, now: [
            self._row(r) for r in conn.execute(
                f"SELECT * FROM runs WHERE state IN ({_ACTIVE_SQL}) "
                "AND (claim_owner IS NULL OR claim_expires_at IS NULL OR claim_expires_at <= ?) "
                "ORDER BY created_at LIMIT ?", (now, int(limit))).fetchall()])

    @staticmethod
    def _resolve(conn: sqlite3.Connection, session_id: str) -> str:
        row = conn.execute("SELECT session_id FROM session_aliases WHERE alias=?", (session_id,)).fetchone()
        return row[0] if row else session_id

    def resolve_session(self, session_id: str) -> str:
        """The session id a run recorded for ``session_id``: itself, or the id it was compacted from."""
        if not session_id:
            return session_id
        return self._read(lambda conn, now: self._resolve(conn, session_id))

    def is_child_session(self, *session_ids: str) -> bool:
        """True when any of ``session_ids`` is (or was compacted from) a Verksted child session."""
        ids = [s for s in session_ids if isinstance(s, str) and s]
        if not ids:
            return False

        def op(conn: sqlite3.Connection, now: float) -> bool:
            return any(conn.execute("SELECT 1 FROM runs WHERE session_id=? LIMIT 1",
                                    (self._resolve(conn, sid),)).fetchone() for sid in ids)
        return self._read(op)

    def add_session_alias(self, old_session_id: str, new_session_id: str) -> bool:
        """Follow a compaction that rotated a parent's or a child's session id; a no-op for
        sessions no run knows."""
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return False

        def op(conn: sqlite3.Connection, now: float) -> bool:
            original = self._resolve(conn, old_session_id)
            if not conn.execute("SELECT 1 FROM runs WHERE session_id=? OR parent_session_id=? LIMIT 1",
                                (original, original)).fetchone():
                return False
            conn.execute("INSERT OR REPLACE INTO session_aliases(alias, session_id) VALUES (?, ?)",
                         (new_session_id, original))
            return True
        return self._write(op)

    # -- claims -------------------------------------------------------------------------------

    def claim(self, run_id: str, owner: str, *, principal: Optional[dict[str, Any]],
              lease_s: float) -> dict[str, Any]:
        """Grant a lease on an active run and raise its claim generation, atomically.

        A live claim of another owner refuses with ``claimed``. ``mode`` is ``start`` while nothing
        was requested from Hermes yet for the current turn (``waiting_for_gui``, ``send_waiting``),
        otherwise ``reconcile``: the claimant may only repeat the stored calls under the stored
        identity, or mark the run ``unknown``.
        """
        if not owner:
            raise StoreError("bad_owner", "claim owner must be a non-empty string")

        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            if row is None:
                raise StoreError("not_found", "no such run")
            if row["state"] in FINAL_STATES:
                raise StoreError("final", f"run is {row['state']}")
            if (row["claim_owner"] and row["claim_owner"] != owner
                    and (row["claim_expires_at"] or 0) > now):
                raise StoreError("claimed", "another window holds a live claim on this run",
                                 expires_at=row["claim_expires_at"])
            generation = int(row["claim_generation"]) + 1
            expires = now + float(lease_s)
            conn.execute(
                "UPDATE runs SET claim_owner=?, claim_generation=?, claim_expires_at=?, "
                "claim_principal=?, updated_at=? WHERE run_id=?",
                (owner, generation, expires, _dumps(principal) if principal is not None else None, now, run_id))
            run = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            return {"generation": generation, "expires_at": expires,
                    "mode": "start" if row["state"] in ("waiting_for_gui", "send_waiting") else "reconcile",
                    "run": run}

        return self._write(op)

    def _fenced(self, conn: sqlite3.Connection, now: float, run_id: str, owner: str,
                generation: int) -> dict[str, Any]:
        row = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
        if row is None:
            raise StoreError("not_found", "no such run")
        if row["claim_owner"] != owner or int(row["claim_generation"]) != int(generation):
            raise StoreError("stale_generation", "this claim was superseded",
                             generation=row["claim_generation"])
        if (row["claim_expires_at"] or 0) <= now:
            raise StoreError("lease_expired", "the claim lease expired; claim again to reconcile")
        return row

    def renew(self, run_id: str, owner: str, generation: int, *, lease_s: float) -> dict[str, Any]:
        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._fenced(conn, now, run_id, owner, generation)
            if row["state"] in FINAL_STATES:
                raise StoreError("final", f"run is {row['state']}")
            expires = now + float(lease_s)
            conn.execute("UPDATE runs SET claim_expires_at=? WHERE run_id=?", (expires, run_id))
            return {"generation": int(generation), "expires_at": expires}
        return self._write(op)

    def release(self, run_id: str, owner: str, generation: int) -> dict[str, Any]:
        """Give a claim up early. The generation stays, so an older confirmation stays locked out."""
        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            if row is None:
                raise StoreError("not_found", "no such run")
            if row["claim_owner"] != owner or int(row["claim_generation"]) != int(generation):
                raise StoreError("stale_generation", "this claim was superseded")
            conn.execute("UPDATE runs SET claim_expires_at=?, updated_at=? WHERE run_id=?", (now, now, run_id))
            return {"released": True}
        return self._write(op)

    # -- stop and follow-up turns ------------------------------------------------------------

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        """Store a stop request (``verksted cancel``), atomically with what it allows.

        ``cancel`` in the answer: ``not_started`` — the prompt was not submitted yet, the run is
        ``cancelled`` now and the Desktop part's next step is refused; ``requested`` — the prompt
        may have reached Hermes, so the window that holds the run asks Hermes to stop it and only
        Hermes' terminal receipt ends it; ``already_final`` — nothing to stop.
        """
        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            if row is None:
                raise StoreError("not_found", "no such run")
            if row["state"] in FINAL_STATES:
                return {"cancel": "already_final", "run": row}
            if row["state"] in CANCEL_AT_ONCE:
                conn.execute(
                    "UPDATE runs SET state='cancelled', state_detail=?, cancel_requested_at=?, finished_at=?, "
                    "updated_at=? WHERE run_id=?",
                    (_dumps({"reason": "cancelled_before_submit", "after": row["state"]}), now, now, now, run_id))
                answer = "not_started"
            else:
                if row["cancel_requested_at"] is None:
                    conn.execute("UPDATE runs SET cancel_requested_at=?, updated_at=? WHERE run_id=?",
                                 (now, now, run_id))
                answer = "requested"
            return {"cancel": answer,
                    "run": self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())}
        return self._write(op)

    def start_send(self, run_id: str, *, task: str, prompt_text: str, max_active: int) -> dict[str, Any]:
        """Store a follow-up turn for the run's child session (``verksted send``) in
        ``send_waiting``, with its new ``submission_id``, before anything is requested.

        Only a run whose last turn ended (``finished``, or ``cancelled`` with a child session) can
        be continued; the ended turn moves to ``turns``. Refuses ``run_active``, ``outcome_unknown``
        (Hermes would queue the turn behind the unresolved one), ``not_sendable``, ``folder_busy``
        and ``too_many_runs``.
        """
        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            if row is None:
                raise StoreError("not_found", "no such run")
            if row["state"] in ACTIVE_STATES:
                raise StoreError("run_active", f"the run is still {row['state']}; wait for it to end first",
                                 state=row["state"])
            if row["state"] == "unknown":
                raise StoreError("outcome_unknown", "the run's last turn has an unknown outcome; the user resolves "
                                                    "it in the child session first", state="unknown")
            if row["state"] not in SENDABLE_STATES or not row["session_id"]:
                raise StoreError("not_sendable", f"a {row['state']} run without a child session cannot be "
                                                 "continued; start a new run", state=row["state"])
            busy = conn.execute(
                f"SELECT run_id FROM runs WHERE target_key=? AND state IN ({_ACTIVE_SQL})",
                (row["target_key"],)).fetchone()
            if busy:
                raise StoreError("folder_busy", "another Verksted run is active in this folder", run_id=busy[0])
            active = conn.execute(f"SELECT COUNT(*) FROM runs WHERE state IN ({_ACTIVE_SQL})").fetchone()[0]
            if active >= max_active:
                raise StoreError("too_many_runs",
                                 f"{active} Verksted runs are active in this profile (limit {max_active})")
            raw = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            conn.execute(
                "INSERT INTO turns (run_id, turn, submission_id, task, prompt_text, state, state_detail, "
                "admission_id, outcome, result_ref, cancel_requested_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(raw[k] for k in ("run_id", "turn_no", "submission_id", "task", "prompt_text", "state",
                                       "state_detail", "admission_id", "outcome", "result_ref",
                                       "cancel_requested_at", "finished_at")))
            turn = int(raw["turn_no"]) + 1
            conn.execute(
                "UPDATE runs SET turn_no=?, submission_id=?, task=?, prompt_text=?, state='send_waiting', "
                "state_detail='{}', admission_id=NULL, outcome=NULL, result_ref=NULL, finished_at=NULL, "
                "cancel_requested_at=NULL, claim_expires_at=?, updated_at=? WHERE run_id=?",
                # The claim of the ended turn expires (its generation stays and fences the old
                # holder), so any window can claim the new turn at once.
                (turn, f"{run_id}.submit.{turn}", task, prompt_text, now, now, run_id))
            return self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
        return self._write(op)

    def turns(self, run_id: str) -> list[dict[str, Any]]:
        """The run's earlier turns, oldest first (the current one is the run row itself)."""
        def op(conn: sqlite3.Connection, now: float) -> list[dict[str, Any]]:
            out = []
            for r in conn.execute("SELECT * FROM turns WHERE run_id=? ORDER BY turn", (run_id,)).fetchall():
                item = dict(r)
                for key in ("state_detail", "result_ref"):
                    if item.get(key) is not None:
                        item[key] = json.loads(item[key])
                out.append(item)
            return out
        return self._read(op)

    # -- steps --------------------------------------------------------------------------------

    def record_step(self, run_id: str, owner: str, generation: int, step: str,
                    data: Optional[dict[str, Any]] = None, *,
                    principal: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Record one Desktop-side step, fenced by owner, claim generation and a live lease.

        ``data["identity"]`` (connection_id, route_profile, target_profile, profile_id,
        instance_id) is stored at ``create_requested`` together with the principal's subject; every
        later step that touches the child session must carry the same identity (``instance_id``
        is recorded but not compared: a restarted gateway gets a new one, and Hermes' create and
        submit idempotency is durable across restarts). ``refused`` and ``unknown`` need no
        identity, so a claimant under another identity can still close a run as ``unknown``.
        """
        data = dict(data or {})
        if step not in STATES:
            raise StoreError("bad_step", f"unknown step {step!r}")

        def op(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
            row = self._fenced(conn, now, run_id, owner, generation)
            if step not in TRANSITIONS.get(row["state"], frozenset()):
                raise StoreError("bad_transition", f"{row['state']} -> {step} is not allowed",
                                 state=row["state"])
            if step == "cancelled" and row["cancel_requested_at"] is None:
                raise StoreError("bad_transition", "only a requested stop ends a run as cancelled", state=row["state"])
            sets: dict[str, Any] = {"state": step, "updated_at": now, "last_known_at": now}
            identity = _identity(data.get("identity"), principal)
            if step == "create_requested":
                if identity is None:
                    raise StoreError("bad_data", "create_requested needs the connection identity")
                sets.update({k: identity.get(k) for k in IDENTITY_FIELDS})
                sets["instance_id"] = identity.get("instance_id")
            elif step in ("session_created", "toolsets_checked", "submit_requested", "running", "finished"):
                _require_same_identity(row, identity)
                if row["state"] == "running" and step == "running" and data.get("admission_id") not in (
                        None, row["admission_id"]):
                    raise StoreError("bad_data", "admission_id of a running run cannot change")
            if step == "session_created":
                sid = data.get("session_id")
                if not isinstance(sid, str) or not sid:
                    raise StoreError("bad_data", "session_created needs session_id")
                sets["session_id"] = sid
                if isinstance(data.get("enabled_toolsets"), list):
                    sets["effective_toolsets"] = _dumps(data["enabled_toolsets"])
            elif step == "toolsets_checked":
                if isinstance(data.get("enabled_toolsets"), list):
                    sets["effective_toolsets"] = _dumps(data["enabled_toolsets"])
                sets["state_detail"] = _dumps({"toolsets": _small(data.get("check"))})
            elif step == "running":
                adm = data.get("admission_id") or row["admission_id"]
                if not isinstance(adm, str) or not adm:
                    raise StoreError("bad_data", "running needs admission_id")
                sets["admission_id"] = adm
                sets["state_detail"] = _dumps({
                    "receipt_status": str(data.get("receipt_status") or "queued"),
                    "prompts": _prompts(data.get("prompts")),
                })
            elif step == "finished":
                outcome = data.get("outcome")
                if not isinstance(outcome, str) or not outcome:
                    raise StoreError("bad_data", "finished needs outcome")
                sets.update(outcome=outcome, finished_at=now, state_detail="{}",
                            result_ref=_dumps(_result_ref(row, data, now)))
            elif step == "cancelled":
                reason = data.get("reason") if isinstance(data.get("reason"), str) and data.get("reason") \
                    else "cancelled_before_submit"
                sets.update(finished_at=now, state_detail=_dumps({"reason": reason, "after": row["state"]}))
            elif step in ("refused", "unknown"):
                reason = data.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise StoreError("bad_data", f"{step} needs reason")
                detail, _ = _clip(data.get("detail"), DETAIL_TEXT_LIMIT)
                extra = {k: data[k] for k in ("extra", "missing", "enabled_toolsets", "field")
                         if isinstance(data.get(k), (list, str))}
                sets.update(finished_at=now, state_detail=_dumps({"reason": reason, "detail": detail, **extra}))
            assignments = ", ".join(f"{k}=?" for k in sets)
            conn.execute(f"UPDATE runs SET {assignments} WHERE run_id=?", (*sets.values(), run_id))
            return self._row(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())

        return self._write(op)


def _result_ref(row: dict[str, Any], data: dict[str, Any], now: float) -> dict[str, Any]:
    """The result reference of a finished turn (v2): the clipped final answer, the closing block's
    summary and the output paths the plugin API checked against the workspace root (card k10), and
    the Rückfragen and approvals that went unanswered (card k8). File contents are never stored."""
    text, truncated = _clip(data.get("final_response"), RESULT_TEXT_LIMIT)
    check = data.get("result") if isinstance(data.get("result"), dict) else {}
    block = check.get("block") if check.get("block") in ("ok", "missing", "unparsable") else "missing"
    detail = check.get("detail") if isinstance(check.get("detail"), str) else None
    if not check:
        detail = "no result check was recorded"
    summary = check.get("summary") if block == "ok" and isinstance(check.get("summary"), str) else None
    summary_cut = False
    if summary is not None:
        summary, summary_cut = _clip(summary, SUMMARY_LIMIT)
    outputs = [_entry(o, ("path", "relative", "kind", "size", "mtime")) for o in _items(check.get("outputs"))]
    missing = [_entry(m, ("path", "reason")) for m in _items(check.get("missing"))]
    return {
        "v": 2, "session_id": row["session_id"], "admission_id": row["admission_id"],
        "final_response": text, "truncated": truncated,
        "block": block, "block_detail": _clip(detail, 500)[0] if detail else None,
        "summary": summary, "outputs": outputs[:OUTPUTS_LIMIT], "missing": missing[:OUTPUTS_LIMIT],
        "result_truncated": bool(check.get("truncated")) or summary_cut
        or len(outputs) > OUTPUTS_LIMIT or len(missing) > OUTPUTS_LIMIT,
        "root": check.get("root") if isinstance(check.get("root"), str) else None,
        "checked_at": now if check else None,
        "unanswered": [_entry(q, ("question",)) for q in _items(data.get("unanswered"))][:NOTES_LIMIT],
        "blocked": [_entry(b, ("command", "reason")) for b in _items(data.get("blocked"))][:NOTES_LIMIT],
    }


def result_view(ref: Any) -> dict[str, Any]:
    """A stored result reference in the v2 shape; a v1 reference (schema 1, before card k10) has
    no checked block and says so instead of guessing one."""
    ref = ref if isinstance(ref, dict) else {}
    if ref.get("v") == 2:
        return ref
    return {"v": 1, "session_id": ref.get("session_id"), "admission_id": ref.get("admission_id"),
            "final_response": ref.get("final_response") or "", "truncated": bool(ref.get("truncated")),
            "block": "missing", "block_detail": "recorded by an earlier Verksted build, before outputs were checked",
            "summary": None, "outputs": [], "missing": [], "result_truncated": False, "root": None,
            "checked_at": None, "unanswered": [], "blocked": []}


def _items(raw: Any) -> list[dict[str, Any]]:
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _entry(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = _clip(value, PATH_LIMIT)[0]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        else:
            out[key] = None
    return out


def _identity(raw: Any, principal: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    out = {k: raw.get(k) for k in ("connection_id", "route_profile", "target_profile", "profile_id", "instance_id")}
    if not all(isinstance(out[k], str) and out[k] for k in ("connection_id", "route_profile", "profile_id")):
        return None
    subject = (principal or {}).get("subject")
    out["subject"] = str(subject) if subject else None
    return out


def _require_same_identity(row: dict[str, Any], identity: Optional[dict[str, Any]]) -> None:
    if identity is None:
        raise StoreError("bad_data", "this step needs the connection identity")
    differs = [k for k in IDENTITY_FIELDS if (row.get(k) or None) != (identity.get(k) or None)]
    if differs:
        raise StoreError("identity_mismatch",
                         "the child session belongs to another connection identity", fields=differs)


def _prompts(raw: Any) -> list[dict[str, Any]]:
    """The open clarify/approval prompts, reduced to what the status shows."""
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or p.get("kind") not in ("clarify", "approval"):
            continue
        item = {"kind": p["kind"], "prompt_id": str(p.get("prompt_id") or "")}
        for key in ("question", "command", "description"):
            if p.get(key):
                item[key] = _clip(p[key], 500)[0]
        if isinstance(p.get("timeout_s"), (int, float)):
            item["timeout_s"] = p["timeout_s"]
        out.append(item)
    return out[:8]


def _small(value: Any) -> Any:
    """Keep a JSON detail bounded."""
    text = _dumps(value)
    return value if len(text) <= DETAIL_TEXT_LIMIT else {"truncated": text[:DETAIL_TEXT_LIMIT]}
