"""
Service Staging : persistance des jobs, lignes, mappings, logs.
Et commit transactionnel final vers les tables cibles.
"""
import json
from datetime import date, datetime, time
from db import get_db_connection
from .schemas import get_schema
try:
    from tenant import current_school_id
except ImportError:
    def current_school_id():
        return 1


def _json_default(o):
    if isinstance(o, (datetime, date, time)):
        return o.isoformat()
    return str(o)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=_json_default)


# ---------- Jobs ----------

def create_job(created_by, source_type, target_table, original_filename=None,
               stored_path=None, external_config=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO import_jobs
              (created_by, source_type, target_table, original_filename, stored_path,
               external_config, status, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s)
        """, (created_by, source_type, target_table, original_filename, stored_path,
              _dump(external_config) if external_config else None,
              current_school_id()))
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def update_job(job_id, **fields):
    if not fields:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sets = ', '.join(f"{k}=%s" for k in fields.keys())
        cur.execute(f"UPDATE import_jobs SET {sets} WHERE id=%s",
                    list(fields.values()) + [job_id])
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_job(job_id):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM import_jobs WHERE id=%s", (job_id,))
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def list_jobs(limit=100):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT j.*, u.email AS creator_email
            FROM import_jobs j
            LEFT JOIN users u ON u.id = j.created_by
            ORDER BY j.created_at DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


# ---------- Mappings ----------

def save_mappings(job_id, mapping_dict):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM import_field_mappings WHERE job_id=%s", (job_id,))
        for src, tgt in mapping_dict.items():
            cur.execute("""
                INSERT INTO import_field_mappings (job_id, source_column, target_field, is_ignored)
                VALUES (%s, %s, %s, %s)
            """, (job_id, src, tgt, 0 if tgt else 1))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_mappings(job_id):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT source_column, target_field, is_ignored FROM import_field_mappings WHERE job_id=%s", (job_id,))
        return {r['source_column']: (None if r['is_ignored'] else r['target_field']) for r in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


# ---------- Staging rows ----------

def save_staging_rows(job_id, rows):
    """Persiste les lignes brutes (avant mapping)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM import_staging_rows WHERE job_id=%s", (job_id,))
        for i, row in enumerate(rows):
            cur.execute("""
                INSERT INTO import_staging_rows (job_id, row_index, raw_payload)
                VALUES (%s, %s, %s)
            """, (job_id, i, _dump(row)))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def update_staging_validation(job_id, validated):
    """validated = list de dicts {row_index, mapped, is_valid, errors}."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for v in validated:
            cur.execute("""
                UPDATE import_staging_rows
                SET mapped_payload=%s, is_valid=%s, validation_errors=%s
                WHERE job_id=%s AND row_index=%s
            """, (_dump(v['mapped']), int(v['is_valid']),
                  _dump(v['errors']) if v['errors'] else None,
                  job_id, v['row_index']))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_staging_rows(job_id, only_valid=None, limit=None):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        q = "SELECT * FROM import_staging_rows WHERE job_id=%s"
        params = [job_id]
        if only_valid is True:
            q += " AND is_valid=1"
        elif only_valid is False:
            q += " AND is_valid=0"
        q += " ORDER BY row_index"
        if limit:
            q += " LIMIT %s"
            params.append(limit)
        cur.execute(q, params)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


# ---------- Logs ----------

def log_action(job_id, user_id, level, action, message=None, context=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO import_logs (job_id, user_id, level, action, message, context)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (job_id, user_id, level, action, message,
              _dump(context) if context else None))
        conn.commit()
    finally:
        cur.close()
        conn.close()
