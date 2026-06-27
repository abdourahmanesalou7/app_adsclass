"""Requêtes cross-tenant pour le superadmin (lecture sans filtre school_id)."""
from datetime import datetime, timedelta
from db import get_db_connection


def _conn():
    return get_db_connection()


def kpis_global():
    """KPIs globaux : nb écoles, abonnements actifs, MRR, écoles expirant <30j, etc."""
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM schools")
        nb_schools = cur.fetchone()['n']

        cur.execute("SELECT COUNT(*) AS n FROM schools WHERE statut='active'")
        nb_active_schools = cur.fetchone()['n']

        cur.execute("""
            SELECT COUNT(*) AS n FROM school_subscriptions
            WHERE status IN ('active','trial') AND ends_at > NOW()
        """)
        nb_active_subs = cur.fetchone()['n']

        cur.execute("""
            SELECT COALESCE(SUM(p.prix_mensuel),0) AS mrr
            FROM school_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.status='active' AND s.ends_at > NOW()
        """)
        mrr = float(cur.fetchone()['mrr'] or 0)

        cur.execute("""
            SELECT COUNT(*) AS n FROM school_subscriptions
            WHERE status IN ('active','trial')
              AND ends_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 30 DAY)
        """)
        nb_expiring = cur.fetchone()['n']

        cur.execute("SELECT COUNT(*) AS n FROM users")
        nb_users = cur.fetchone()['n']

        cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='etudiant'")
        nb_students = cur.fetchone()['n']

        cur.execute("""
            SELECT COALESCE(SUM(montant),0) AS revenue FROM subscription_payments
            WHERE statut='payé' AND paid_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """)
        revenue_30d = float(cur.fetchone()['revenue'] or 0)

        return {
            'nb_schools': nb_schools,
            'nb_active_schools': nb_active_schools,
            'nb_active_subs': nb_active_subs,
            'mrr': mrr,
            'nb_expiring': nb_expiring,
            'nb_users': nb_users,
            'nb_students': nb_students,
            'revenue_30d': revenue_30d,
        }
    finally:
        cur.close()
        conn.close()


def list_schools_full():
    """Liste écoles + abonnement actif + comptes users (vue admin)."""
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT sc.*,
                   sub.id AS sub_id, sub.status AS sub_status, sub.ends_at AS sub_ends_at,
                   sub.started_at AS sub_started_at, sub.auto_renew,
                   p.nom AS plan_nom, p.code AS plan_code, p.prix_mensuel,
                   (SELECT COUNT(*) FROM users u WHERE u.school_id=sc.id) AS nb_users,
                   (SELECT COUNT(*) FROM users u WHERE u.school_id=sc.id AND u.role='etudiant') AS nb_students,
                   DATEDIFF(sub.ends_at, NOW()) AS days_left
            FROM schools sc
            LEFT JOIN school_subscriptions sub
              ON sub.school_id = sc.id
              AND sub.id = (SELECT id FROM school_subscriptions
                            WHERE school_id = sc.id
                            ORDER BY ends_at DESC LIMIT 1)
            LEFT JOIN subscription_plans p ON p.id = sub.plan_id
            ORDER BY sc.date_creation DESC
        """)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def get_school(school_id):
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM schools WHERE id=%s", (school_id,))
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def list_subscriptions_for(school_id):
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT s.*, p.nom AS plan_nom, p.code AS plan_code, p.prix_mensuel
            FROM school_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.school_id=%s
            ORDER BY s.ends_at DESC
        """, (school_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def list_payments_for(school_id, limit=50):
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT * FROM subscription_payments
            WHERE school_id=%s ORDER BY paid_at DESC LIMIT %s
        """, (school_id, int(limit)))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def list_admins_for(school_id):
    """Comptes administratifs (role='admin') de l'école + nom du rôle RBAC."""
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT u.id, u.nom, u.prenom, u.email, u.identifiant,
                   u.password_temp, u.must_change_password,
                   r.nom AS role_nom, r.priorite
            FROM users u
            LEFT JOIN admin_roles r ON u.admin_role_id = r.id
            WHERE u.school_id = %s AND u.role = 'admin'
            ORDER BY r.priorite DESC, u.nom
        """, (school_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def recent_signups(limit=10):
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, nom, code, statut, date_creation
            FROM schools ORDER BY date_creation DESC LIMIT %s
        """, (int(limit),))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()
