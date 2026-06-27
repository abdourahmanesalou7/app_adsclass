"""
Blueprint Flask /admin/imports — wizard d'import multi-sources.
Workflow : upload/config → mapping → preview/validation → commit.
"""
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, abort, current_app
)

from permissions import PermissionManager
from .connectors import get_connector
from .etl.mapper import suggest_mapping, apply_mapping
from .etl.validator import validate_row
from .schemas import list_schemas, get_schema
from .security import (
    validate_filename, validate_file_size, ImportSecurityError,
)
from . import staging
from .commit import commit_job

bp = Blueprint('imports', __name__, url_prefix='/admin/imports')

UPLOAD_SUBDIR = 'imports'


def _require_admin_with(permission):
    if 'user_id' not in session:
        flash("Connectez-vous.", "warning")
        return redirect(url_for('login'))
    if session.get('role') != 'admin':
        flash("Accès réservé aux administrateurs.", "danger")
        return redirect(url_for('login'))
    uid = session['user_id']
    if not PermissionManager.has_permission(uid, permission):
        flash(f"Permission manquante : {permission}", "danger")
        return redirect(url_for('admin_home'))
    return None


def _upload_dir():
    base = os.path.join(current_app.root_path, 'uploads', UPLOAD_SUBDIR)
    os.makedirs(base, exist_ok=True)
    return base


# ---------------- Liste des jobs ----------------
@bp.route('/')
def index():
    guard = _require_admin_with('imports.view')
    if guard:
        return guard
    jobs = staging.list_jobs(limit=200)
    return render_template('admin_imports.html',
                           jobs=jobs, schemas=list_schemas())


# ---------------- Nouveau : choix source + schéma ----------------
@bp.route('/new', methods=['GET', 'POST'])
def new():
    guard = _require_admin_with('imports.execute')
    if guard:
        return guard

    if request.method == 'POST':
        source_type = request.form.get('source_type')
        target = request.form.get('target_schema')
        if source_type not in ('excel', 'csv', 'word', 'mysql', 'api'):
            flash("Source invalide.", "danger")
            return redirect(url_for('imports.new'))
        if not get_schema(target):
            flash("Schéma cible invalide.", "danger")
            return redirect(url_for('imports.new'))

        try:
            if source_type in ('excel', 'csv', 'word'):
                f = request.files.get('file')
                if not f or not f.filename:
                    flash("Fichier manquant.", "danger")
                    return redirect(url_for('imports.new'))
                validate_filename(f.filename, source_type)
                safe_name = secure_filename(f.filename)
                stamped = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
                stored_path = os.path.join(_upload_dir(), stamped)
                f.save(stored_path)
                validate_file_size(stored_path)
                job_id = staging.create_job(
                    session['user_id'], source_type, target,
                    original_filename=f.filename, stored_path=stored_path,
                )
                _extract_and_stage(job_id, source_type, {'filepath': stored_path})
            elif source_type == 'mysql':
                cfg = {
                    'host': request.form.get('mysql_host', '').strip(),
                    'user': request.form.get('mysql_user', '').strip(),
                    'password': request.form.get('mysql_password', ''),
                    'database': request.form.get('mysql_database', '').strip(),
                    'port': request.form.get('mysql_port', '3306').strip() or '3306',
                    'query': request.form.get('mysql_query', '').strip(),
                }
                public_cfg = {k: v for k, v in cfg.items() if k != 'password'}
                job_id = staging.create_job(
                    session['user_id'], source_type, target,
                    external_config=public_cfg,
                )
                _extract_and_stage(job_id, source_type, cfg)
            elif source_type == 'api':
                cfg = {
                    'url': request.form.get('api_url', '').strip(),
                    'method': request.form.get('api_method', 'GET'),
                    'headers': _parse_kv(request.form.get('api_headers', '')),
                    'params': _parse_kv(request.form.get('api_params', '')),
                    'data_path': request.form.get('api_data_path', '').strip() or None,
                }
                job_id = staging.create_job(
                    session['user_id'], source_type, target,
                    external_config={k: v for k, v in cfg.items() if k != 'headers'},
                )
                _extract_and_stage(job_id, source_type, cfg)

            staging.log_action(job_id, session['user_id'], 'info', 'extract',
                               f"Source {source_type} extraite vers staging")
            return redirect(url_for('imports.mapping', job_id=job_id))

        except ImportSecurityError as e:
            flash(f"Sécurité : {e}", "danger")
            return redirect(url_for('imports.new'))
        except Exception as e:
            flash(f"Erreur extraction : {e}", "danger")
            return redirect(url_for('imports.new'))

    return render_template('admin_import_new.html', schemas=list_schemas())


def _parse_kv(text):
    """Parse 'Key: Value' multi-lignes en dict."""
    out = {}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        k, _, v = line.partition(':')
        out[k.strip()] = v.strip()
    return out


def _extract_and_stage(job_id, source_type, params):
    ConnectorCls = get_connector(source_type)
    connector = ConnectorCls()
    result = connector.extract(**params)
    rows = result.get('rows', [])
    staging.save_staging_rows(job_id, rows)
    staging.update_job(job_id, total_rows=len(rows), status='parsed')
    # Mapping auto-suggéré
    schema_key = staging.get_job(job_id)['target_table']
    schema = get_schema(schema_key)
    headers = result.get('headers', [])
    mapping = suggest_mapping(headers, schema)
    staging.save_mappings(job_id, mapping)
    staging.update_job(job_id, status='mapped')


# ---------------- Mapping (revue/édition) ----------------
@bp.route('/<int:job_id>/mapping', methods=['GET', 'POST'])
def mapping(job_id):
    guard = _require_admin_with('imports.execute')
    if guard:
        return guard
    job = staging.get_job(job_id)
    if not job:
        abort(404)
    schema = get_schema(job['target_table'])
    if not schema:
        flash("Schéma cible inconnu.", "danger")
        return redirect(url_for('imports.index'))

    current_mapping = staging.get_mappings(job_id)

    if request.method == 'POST':
        new_mapping = {}
        for src in current_mapping.keys():
            field_key = f"map__{src}"
            target = request.form.get(field_key, '').strip() or None
            if target == '__ignore__':
                target = None
            new_mapping[src] = target
        staging.save_mappings(job_id, new_mapping)
        staging.log_action(job_id, session['user_id'], 'info', 'mapping_saved',
                           f"{sum(1 for v in new_mapping.values() if v)} colonnes mappées")
        return redirect(url_for('imports.preview', job_id=job_id))

    available_fields = list(schema['fields'].keys())
    sample_rows = staging.get_staging_rows(job_id, limit=5)
    return render_template('admin_import_mapping.html',
                           job=job, schema=schema, mapping=current_mapping,
                           available_fields=available_fields,
                           sample_rows=sample_rows)


# ---------------- Preview / Validation ----------------
@bp.route('/<int:job_id>/preview', methods=['GET', 'POST'])
def preview(job_id):
    guard = _require_admin_with('imports.execute')
    if guard:
        return guard
    job = staging.get_job(job_id)
    if not job:
        abort(404)
    schema = get_schema(job['target_table'])
    mapping_dict = staging.get_mappings(job_id)
    rows = staging.get_staging_rows(job_id)

    validated = []
    valid_count = 0
    invalid_count = 0
    for r in rows:
        raw = json.loads(r['raw_payload']) if isinstance(r['raw_payload'], str) else r['raw_payload']
        mapped = apply_mapping(raw, mapping_dict, schema)
        is_valid, transformed, errors = validate_row(mapped, schema)
        validated.append({
            'row_index': r['row_index'],
            'mapped': transformed,
            'is_valid': is_valid,
            'errors': errors,
        })
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

    staging.update_staging_validation(job_id, validated)
    staging.update_job(job_id, valid_rows=valid_count, invalid_rows=invalid_count,
                       status='validated')

    if request.method == 'POST':
        if request.form.get('confirm') == 'yes':
            try:
                result = commit_job(job_id, session['user_id'], only_valid=True)
                created_creds = result.get('created_credentials') or []
                if created_creds:
                    flash(
                        f"✅ {result['committed']} ligne(s) importée(s) — "
                        f"{len(created_creds)} compte(s) créé(s) avec identifiants temporaires.",
                        "success",
                    )
                    return redirect(url_for('imports.credentials_print', job_id=job_id))
                flash(f"✅ {result['committed']} ligne(s) importée(s), {result['skipped']} ignorée(s).", "success")
                return redirect(url_for('imports.index'))
            except Exception as e:
                flash(f"Erreur commit : {e}", "danger")
                return redirect(url_for('imports.preview', job_id=job_id))

    return render_template('admin_import_preview.html',
                           job=job, schema=schema, validated=validated[:200],
                           total_valid=valid_count, total_invalid=invalid_count,
                           total=len(validated))


# ---------------- Détail / logs ----------------
@bp.route('/<int:job_id>')
def detail(job_id):
    guard = _require_admin_with('imports.view')
    if guard:
        return guard
    job = staging.get_job(job_id)
    if not job:
        abort(404)
    rows = staging.get_staging_rows(job_id, limit=500)
    mapping_dict = staging.get_mappings(job_id)
    return render_template('admin_import_detail.html',
                           job=job, rows=rows, mapping=mapping_dict)



# ---------------- Impression des identifiants générés ----------------
@bp.route('/<int:job_id>/credentials/print')
def credentials_print(job_id):
    guard = _require_admin_with('imports.view')
    if guard:
        return guard
    job = staging.get_job(job_id)
    if not job:
        abort(404)
    schema = get_schema(job['target_table'])
    if not schema or not schema.get('auto_credentials'):
        flash("Aucun identifiant à imprimer pour ce job.", "info")
        return redirect(url_for('imports.detail', job_id=job_id))

    from db import get_db_connection
    from student_enrollment_service import ensure_student_account_columns

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        ensure_student_account_columns(cur)
        cur.execute(
            """
            SELECT u.id, u.prenom, u.nom, u.email, u.identifiant, u.password_temp,
                   u.filiere, u.niveau, u.classe, u.must_change_password, u.role
            FROM import_staging_rows s
            JOIN users u ON u.id = s.target_row_id
            WHERE s.job_id = %s AND s.committed = 1 AND s.target_row_id IS NOT NULL
            ORDER BY u.nom, u.prenom
            """,
            (job_id,),
        )
        users = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not users:
        flash("Aucun utilisateur trouvé pour ce job.", "info")
        return redirect(url_for('imports.detail', job_id=job_id))

    return render_template(
        'admin_student_credentials_print.html',
        students=users, single=False,
        nom_classe=users[0].get('classe') or '',
        filiere=users[0].get('filiere') or '',
        niveau=users[0].get('niveau') or '',
    )
