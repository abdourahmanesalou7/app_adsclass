"""
Module SaaS Subscriptions ADSClass — Phase 4.

API publique :
    get_active_subscription(school_id) -> dict | None
    is_active(school_id)               -> bool
    days_remaining(school_id)          -> int (négatif si expiré)
    list_plans()                       -> list[dict]
    create_subscription(...)           -> id
    extend_subscription(school_id, days)
    suspend_subscription(school_id) / reactivate_subscription(school_id)
    init_app(flask_app)                -> hooks middleware + context_processor

Le middleware bloque l'accès si l'école a expiré, sauf :
- pas connecté (login/static accessibles)
- superadmin
- routes en BYPASS_PATHS
"""
import json
from datetime import datetime, timedelta
from flask import session, redirect, url_for, request, g, flash
from db import get_db_connection


def _parse_json_field(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v

try:
    from tenant import current_school_id
except ImportError:
    def current_school_id():
        return 1

GRACE_DAYS = 3  # tolérance après expiration avant blocage dur
BYPASS_PATHS = ('/login', '/logout', '/static/', '/subscription/', '/superadmin/',
                '/favicon.ico', '/api/health')


def _conn():
    return get_db_connection()


def get_active_subscription(school_id=None):
    """Retourne l'abonnement actif (le plus récent non expiré) ou None."""
    sid = school_id or current_school_id()
    conn = _conn()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT s.*, p.code AS plan_code, p.nom AS plan_nom,
                   p.max_students, p.max_users, p.modules, p.features,
                   p.prix_mensuel, p.prix_annuel, p.devise AS plan_devise
            FROM school_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.school_id = %s
              AND s.status IN ('active','trial')
            ORDER BY s.ends_at DESC
            LIMIT 1
        """, (sid,))
        row = cur.fetchone()
        if row:
            row['modules'] = _parse_json_field(row.get('modules'))
            row['features'] = _parse_json_field(row.get('features'))
        return row
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def days_remaining(school_id=None):
    sub = get_active_subscription(school_id)
    if not sub or not sub.get('ends_at'):
        return -9999
    delta = sub['ends_at'] - datetime.now()
    return delta.days


def is_active(school_id=None):
    """Actif si abonnement existe ET (ends_at + grace) > now ET status valide."""
    sub = get_active_subscription(school_id)
    if not sub:
        return False
    if sub['status'] not in ('active', 'trial'):
        return False
    return sub['ends_at'] + timedelta(days=GRACE_DAYS) > datetime.now()


def list_plans(active_only=True):
    conn = _conn()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        if active_only:
            cur.execute("SELECT * FROM subscription_plans WHERE actif=1 ORDER BY prix_mensuel ASC")
        else:
            cur.execute("SELECT * FROM subscription_plans ORDER BY prix_mensuel ASC")
        rows = cur.fetchall()
        for r in rows:
            r['modules'] = _parse_json_field(r.get('modules'))
            r['features'] = _parse_json_field(r.get('features'))
        return rows
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def create_subscription(school_id, plan_id, duration_days=None, status='active',
                        created_by=None, notes=None, auto_renew=False):
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        if duration_days is None:
            cur.execute("SELECT duree_defaut_jours FROM subscription_plans WHERE id=%s", (plan_id,))
            row = cur.fetchone()
            duration_days = (row or {}).get('duree_defaut_jours', 30)
        started = datetime.now()
        ends = started + timedelta(days=int(duration_days))
        cur.execute("""
            INSERT INTO school_subscriptions
              (school_id, plan_id, started_at, ends_at, status, auto_renew, created_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (school_id, plan_id, started, ends, status, 1 if auto_renew else 0, created_by, notes))
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def extend_subscription(school_id, additional_days, created_by=None):
    sub = get_active_subscription(school_id)
    if not sub:
        return False
    new_end = max(sub['ends_at'], datetime.now()) + timedelta(days=int(additional_days))
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE school_subscriptions SET ends_at=%s, status='active' WHERE id=%s AND school_id=%s",
                    (new_end, sub['id'], school_id))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def _update_status(school_id, status):
    sub = get_active_subscription(school_id)
    if not sub:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE school_subscriptions SET status=%s WHERE id=%s AND school_id=%s", (status, sub['id'], school_id))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def suspend_subscription(school_id):
    return _update_status(school_id, 'suspended')


def reactivate_subscription(school_id):
    return _update_status(school_id, 'active')


def cancel_subscription(school_id):
    return _update_status(school_id, 'cancelled')


def init_app(flask_app):
    """Middleware Flask : blocage si abonnement expiré (hors superadmin/bypass)."""

    @flask_app.before_request
    def _subscription_guard():
        # Bypass paths
        path = request.path or ''
        for pfx in BYPASS_PATHS:
            if path.startswith(pfx):
                return None
        # Pas connecté → laisse le décorateur login_required gérer
        if not session.get('user_id'):
            return None
        # Superadmin jamais bloqué
        if session.get('role') == 'superadmin':
            return None
        # Vérification abonnement
        try:
            if not is_active(session.get('school_id') or 1):
                return redirect(url_for('subscription.subscription_blocked'))
        except Exception:
            return None
        return None

    @flask_app.context_processor
    def _inject_subscription():
        try:
            sub = get_active_subscription()
            return {
                'current_subscription': sub,
                'subscription_days_left': days_remaining() if sub else None,
                'subscription_active': is_active(),
            }
        except Exception:
            return {'current_subscription': None, 'subscription_days_left': None, 'subscription_active': True}
