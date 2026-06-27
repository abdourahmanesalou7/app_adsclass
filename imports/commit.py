"""
Commit final transactionnel vers les tables cibles.
- Whitelist stricte (schemas.py)
- Requêtes paramétrées
- Upsert via unique_keys si défini
- Tables liées (users + *_profiles) en cascade dans la même transaction
"""
import json
from datetime import date, datetime, time
from db import get_db_connection
from .schemas import get_schema
from . import staging
from .credentials import enrich_user_payload, post_enroll_student
try:
    from tenant import is_tenant_table, current_school_id
except ImportError:
    def is_tenant_table(_t):
        return False
    def current_school_id():
        return None


def _json_loads_safe(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _split_by_table(transformed_row, schema):
    """Sépare les valeurs transformées par table cible."""
    by_table = {t: {} for t in schema['target_tables']}
    fields = schema['fields']
    for fname, val in transformed_row.items():
        if fname in fields:
            table = fields[fname]['table']
            by_table.setdefault(table, {})[fname] = val
        else:
            # fixed_values comme 'role' -> users par défaut
            by_table.setdefault(schema['target_tables'][0], {})[fname] = val
    return by_table


def _find_existing_user_id(cursor, by_table, unique_keys):
    """Cherche un user existant pour upsert basé sur unique_keys côté users."""
    if 'users' not in by_table or not unique_keys:
        return None
    where_parts, vals = [], []
    for key in unique_keys:
        if key in by_table['users'] and by_table['users'][key]:
            where_parts.append(f"{key}=%s")
            vals.append(by_table['users'][key])
    if not where_parts:
        return None
    cursor.execute(f"SELECT id FROM users WHERE {' AND '.join(where_parts)} LIMIT 1", vals)
    row = cursor.fetchone()
    if not row:
        return None
    return row['id'] if isinstance(row, dict) else row[0]


def _insert(cursor, table, data):
    if not data:
        return None
    cols = list(data.keys())
    placeholders = ', '.join(['%s'] * len(cols))
    cursor.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        [data[c] for c in cols],
    )
    return cursor.lastrowid


def _update(cursor, table, data, where_col, where_val):
    if not data:
        return
    sets = ', '.join(f"{c}=%s" for c in data.keys())
    cursor.execute(
        f"UPDATE {table} SET {sets} WHERE {where_col}=%s",
        list(data.values()) + [where_val],
    )


def commit_job(job_id, user_id, only_valid=True):
    """
    Insère les lignes valides du staging dans les tables cibles.
    Retourne dict {committed, skipped, errors}.
    """
    job = staging.get_job(job_id)
    if not job:
        raise ValueError("Job introuvable")
    schema_key = job['target_table']
    schema = get_schema(schema_key)
    if not schema:
        raise ValueError(f"Schéma '{schema_key}' inconnu")

    # School du job (source de vérité : school_id du job, sinon session admin).
    # Fail-closed : aucun défaut implicite (jamais de school_id=1).
    job_school_id = job.get('school_id') or current_school_id()
    if not job_school_id:
        raise ValueError("school_id introuvable pour ce job d'import : "
                         "impossible de rattacher les données à une école.")

    rows = staging.get_staging_rows(job_id, only_valid=True if only_valid else None)
    committed = 0
    skipped = 0
    errors = []

    conn = get_db_connection()
    conn.autocommit = False
    cur = conn.cursor(dictionary=True)
    created_credentials = []

    try:
        for r in rows:
            mapped = _json_loads_safe(r['mapped_payload']) or {}
            if not mapped:
                skipped += 1
                continue
            by_table = _split_by_table(mapped, schema)

            # Tenant stamping : injecte school_id sur chaque table cible compatible
            for _tbl, _data in by_table.items():
                if _data and is_tenant_table(_tbl) and not _data.get('school_id'):
                    _data['school_id'] = job_school_id

            try:
                # Cas spécial : users + *_profiles
                if 'users' in schema['target_tables']:
                    existing_id = _find_existing_user_id(cur, by_table, schema.get('unique_keys') or [])
                    auto_creds = bool(schema.get('auto_credentials'))
                    users_data = by_table.get('users', {})
                    profile_table = next((t for t in schema['target_tables'] if t != 'users'), None)
                    profile_data = by_table.get(profile_table, {}) if profile_table else {}
                    row_credentials = None

                    if existing_id:
                        # Ne pas écraser le mot de passe si non fourni
                        if 'password' in users_data and not users_data['password']:
                            users_data.pop('password')
                        _update(cur, 'users', users_data, 'id', existing_id)
                        user_pk = existing_id
                    else:
                        if auto_creds:
                            users_data, profile_data, row_credentials = enrich_user_payload(
                                cur, schema_key, users_data, profile_data,
                                force_pro_email=True,
                            )
                            if profile_table:
                                by_table[profile_table] = profile_data
                        elif not users_data.get('password'):
                            users_data['password'] = ''  # NOT NULL fallback
                        by_table['users'] = users_data
                        user_pk = _insert(cur, 'users', users_data)
                        if row_credentials:
                            row_credentials['user_id'] = user_pk
                            created_credentials.append(row_credentials)

                    # Profil lié
                    for t in schema['target_tables']:
                        if t == 'users':
                            continue
                        profile_data = by_table.get(t, {})
                        if not profile_data:
                            continue
                        cur.execute(f"SELECT 1 FROM {t} WHERE user_id=%s", (user_pk,))
                        if cur.fetchone():
                            _update(cur, t, profile_data, 'user_id', user_pk)
                        else:
                            profile_data['user_id'] = user_pk
                            _insert(cur, t, profile_data)

                    # Inscription auto aux cours pour les étudiants nouvellement créés
                    if auto_creds and not existing_id and schema_key == 'students':
                        post_enroll_student(cur, user_pk, users_data)

                    cur.execute(
                        "UPDATE import_staging_rows SET committed=1, target_row_id=%s WHERE id=%s",
                        (user_pk, r['id']),
                    )
                else:
                    # Tables simples (courses, paiements, depenses…)
                    target_table = schema['target_tables'][0]
                    new_id = _insert(cur, target_table, by_table.get(target_table, {}))
                    cur.execute(
                        "UPDATE import_staging_rows SET committed=1, target_row_id=%s WHERE id=%s",
                        (new_id, r['id']),
                    )
                committed += 1
            except Exception as e:
                errors.append({'row_index': r['row_index'], 'error': str(e)})
                skipped += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    staging.update_job(
        job_id,
        committed_rows=committed,
        status='committed' if committed and not errors else ('failed' if errors and not committed else 'committed'),
        committed_at=datetime.now(),
        error_message=(json.dumps(errors[:20], ensure_ascii=False) if errors else None),
    )
    staging.log_action(job_id, user_id, 'info' if not errors else 'warning',
                       'commit', f"{committed} ligne(s) commit, {skipped} ignorée(s)",
                       {'errors_sample': errors[:10]})
    return {
        'committed': committed,
        'skipped': skipped,
        'errors': errors,
        'created_credentials': created_credentials,
    }
