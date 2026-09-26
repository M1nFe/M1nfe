"""시연용 모두온 DB(SQLite, 메모리).

raw(원본) → staging(AI·파서 결과, 검수 전) → canonical(확정) → calc(계산 결과) 계층을 테이블 접두어로 나눈다.
실제 설계의 'DB 권한 방화벽'을 SQLite authorizer로 흉내 낸다. 역할(role)마다 쓸 수 있는 테이블이 다르다.
"""
import sqlite3
from contextlib import contextmanager

SCHEMA = """
create table raw_file(id integer primary key, partner text, kind text, filename text, sha256 text,
  injection_suspect int default 0);
create table staging_mention(id integer primary key, raw_file_id int, partner text, category text,
  raw_name text, model_code text, match_state text default 'unmatched', match_path text, product_id text,
  ai_decision text, ai_grade text);
create table staging_record(id integer primary key, raw_file_id int, mention_id int, partner text,
  field_code text, condition_key text, value_int int, unit text, value_text text, loc text,
  evidence_text text, extractor text, grade text, checks text, state text default 'extracted',
  human_edited int default 0, note text);
create table staging_anomaly(id integer primary key, record_id int, rule_code text, severity text,
  reason text, prev_value int, new_value int, ai_cause text, ai_explanation text, status text default 'open');
create table staging_notice(id integer primary key, raw_file_id int, partner text, change_type text,
  product_ref_raw text, product_id text, new_value_int int, effective_from text, evidence_text text,
  status text default 'needs_confirmation');
create table canonical_product(id text primary key, category text, vendor text, name text,
  model_code text, storage_gb text, color text, variant text);
create table canonical_alias(partner text, alias text, product_id text, approved_by text);
create table canonical_price(id integer primary key, partner text, product_id text, field_code text,
  condition_key text, value_int int, unit text, month text, source text, approved_by text,
  unique(partner, product_id, field_code, condition_key, month));
create table calc_result(id integer primary key, formula text, version text, as_of text, partner text,
  product_id text, inputs text, result int, input_hash text);
create table audit_log(id integer primary key, actor text, action text, detail text);
"""

# 역할별 권한. write: 쓸 수 있는 테이블 접두어, no_read: 읽을 수 없는 테이블 접두어
ROLES = {
    "setup":         {"write": ("",), "no_read": ()},
    # 수집·AI 워커: raw/staging에만 쓸 수 있다. canonical·calc에는 쓸 수 없다.
    "ingest_worker": {"write": ("raw_", "staging_", "audit_"), "no_read": ()},
    # 검수자(사람)의 승인 함수: staging 상태 변경 + canonical 반영
    "reviewer":      {"write": ("staging_", "canonical_", "audit_"), "no_read": ()},
    # 계산 엔진: 확정 데이터만 읽고 계산 결과만 쓴다. staging·raw는 읽을 수조차 없다.
    "calc_engine":   {"write": ("calc_",), "no_read": ("staging_", "raw_")},
    # 자연어 조회: 읽기 전용
    "nlq_reader":    {"write": (), "no_read": ("raw_",)},
}

_WRITE_ACTIONS = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}


class Store:
    def __init__(self):
        # cached_statements=0: 역할이 바뀌면 문장을 다시 준비(prepare)해서 권한 검사를 다시 받게 한다
        self.conn = sqlite3.connect(":memory:", cached_statements=0)
        self.conn.row_factory = sqlite3.Row
        self.role_name = "setup"
        self.conn.executescript(SCHEMA)
        self.conn.set_authorizer(self._authorize)

    def _authorize(self, action, arg1, arg2, dbname, source):
        rule = ROLES[self.role_name]
        table = arg1 or ""
        if table.startswith("sqlite_"):
            return sqlite3.SQLITE_OK
        if action in _WRITE_ACTIONS and not any(table.startswith(p) for p in rule["write"]):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and any(table.startswith(p) for p in rule["no_read"]):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def role(self, name: str):
        prev = self.role_name
        self.role_name = name
        self.conn.set_authorizer(self._authorize)   # 준비된 문장을 만료시켜 권한을 다시 검사
        try:
            yield self
        finally:
            self.role_name = prev
            self.conn.set_authorizer(self._authorize)

    def exec(self, sql: str, params=()) -> int:
        cur = self.conn.execute(sql, params)
        return cur.lastrowid

    def query(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def audit(self, actor: str, action: str, detail: str):
        self.exec("insert into audit_log(actor, action, detail) values (?,?,?)", (actor, action, detail))
