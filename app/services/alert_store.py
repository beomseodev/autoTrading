"""Short PostgreSQL transactions; no external HTTP is performed while connected."""
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import HTTPException


def database_ready():
    return bool(os.getenv("ALERT_DATABASE_URL"))


@contextmanager
def database(url=None):
    url = url or os.getenv("ALERT_DATABASE_URL")
    if not url:
        raise HTTPException(503, "관심종목 알림 저장소를 준비 중입니다. 기존 대시보드는 계속 이용할 수 있습니다.")
    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            yield conn
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "이미 등록된 종목 또는 조건입니다.") from None
    except psycopg.Error:
        raise HTTPException(503, "알림 저장소 연결에 실패했습니다. 입력은 유지됩니다.") from None


def migrate(url):
    with database(url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(810231)")
        conn.execute("CREATE TABLE IF NOT EXISTS alert_schema_versions (name text PRIMARY KEY, checksum text NOT NULL)")
        for path in sorted((Path(__file__).parents[2] / "migrations").glob("*.sql")):
            source = path.read_text(encoding="utf-8")
            checksum = sha256(source.encode()).hexdigest()
            row = conn.execute("SELECT checksum FROM alert_schema_versions WHERE name=%s", (path.name,)).fetchone()
            if row and row["checksum"] != checksum:
                raise RuntimeError("이미 적용한 마이그레이션의 내용이 변경되었습니다.")
            if not row:
                conn.execute(source)
                conn.execute("INSERT INTO alert_schema_versions VALUES (%s,%s)", (path.name, checksum))


def sync_user(session):
    with database() as conn:
        conn.execute("INSERT INTO alert_users(id,google_sub,email) VALUES (%s,%s,%s) ON CONFLICT(google_sub) DO NOTHING",
                     (uuid4(), session["sub"], session["email"]))
        # Email changes revoke consent and old unsubscribe links, never auto-subscribe.
        conn.execute("""UPDATE alert_users SET email=%s, consent=false, revision=revision+1,
                     unsubscribe_version=unsubscribe_version+1 WHERE google_sub=%s AND email<>%s""",
                     (session["email"], session["sub"], session["email"]))
        return conn.execute("SELECT * FROM alert_users WHERE google_sub=%s", (session["sub"],)).fetchone()


def owned(conn, table, identity, user_id, *, lock=False):
    if table not in ("alert_watchlist", "alert_rules"):
        raise ValueError("invalid table")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=%s AND user_id=%s AND NOT archived" +
                       (" FOR UPDATE" if lock else ""), (identity, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "알림 항목을 찾을 수 없습니다.")
    return row


def lock_user(conn, user_id):
    return conn.execute("SELECT * FROM alert_users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()


def check_revision(row, revision):
    if row["revision"] != revision:
        raise HTTPException(409, "다른 화면에서 설정이 변경되었습니다. 입력을 보관하고 새로고침해주세요.")


def legacy_consolidated():
    """Only called for configured deployments; failure must defer old mail."""
    with database() as conn:
        row = conn.execute("SELECT legacy_consolidated FROM alert_users WHERE lower(email)=lower(%s)",
                           (os.getenv("GOOGLE_OWNER_EMAIL", ""),)).fetchone()
        return bool(row and row["legacy_consolidated"])


def json_value(value):
    return Jsonb(value)
