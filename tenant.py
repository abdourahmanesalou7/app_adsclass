"""
Module Multi-Tenant ADSClass.

Source unique de vérité pour `school_id` à l'échelle de la requête.

Usage :
    from tenant import current_school_id, scope_where, with_school

    sid = current_school_id()                   # int (école courante)
    where, params = scope_where('users')         # 'school_id=%s', (sid,)
    data = with_school({'nom': 'X'})             # injecte school_id dans dict
"""
from flask import session, g, has_request_context
from db import get_db_connection

DEFAULT_SCHOOL_ID = 1

# Cache en mémoire de la liste des tables qui possèdent réellement school_id
_TENANT_COLUMNS_CACHE = None


def _load_tenant_tables():
    """Liste les tables possédant une colonne `school_id` (cache process)."""
    global _TENANT_COLUMNS_CACHE
    if _TENANT_COLUMNS_CACHE is not None:
        return _TENANT_COLUMNS_CACHE
    conn = get_db_connection()
    if not conn:
        return set()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'school_id'
        """)
        _TENANT_COLUMNS_CACHE = {r[0] for r in cur.fetchall()}
        return _TENANT_COLUMNS_CACHE
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def refresh_tenant_cache():
    """À appeler après ajout/suppression de school_id sur une table."""
    global _TENANT_COLUMNS_CACHE
    _TENANT_COLUMNS_CACHE = None


def is_tenant_table(table_name):
    return table_name in _load_tenant_tables()


def current_school_id():
    """
    Retourne l'école courante.
    Ordre de résolution : g.school_id → session['school_id'] → DEFAULT_SCHOOL_ID.
    Toujours un entier ≥ 1 (jamais None) → garantit que les requêtes filtrent.
    """
    if has_request_context():
        sid = getattr(g, 'school_id', None)
        if sid:
            return int(sid)
        sid = session.get('school_id')
        if sid:
            return int(sid)
    return DEFAULT_SCHOOL_ID


def set_session_school(school_id):
    """Définit l'école active pour la session courante (au login)."""
    session['school_id'] = int(school_id or DEFAULT_SCHOOL_ID)


def with_school(data, school_id=None):
    """
    Injecte school_id dans un dict de valeurs avant INSERT.
    Ne modifie pas la valeur si déjà présente et non None.
    """
    sid = school_id if school_id is not None else current_school_id()
    data = dict(data) if data else {}
    if not data.get('school_id'):
        data['school_id'] = sid
    return data


def scope_where(table_name, alias=None, school_id=None):
    """
    Retourne (where_fragment, params_tuple) pour filtrer une table par tenant.
    Si la table ne porte pas school_id : ('1=1', ()).

    Exemple :
        w, p = scope_where('users')
        cur.execute(f"SELECT * FROM users WHERE {w} AND role='etudiant'", p)
    """
    if not is_tenant_table(table_name):
        return ('1=1', ())
    sid = school_id if school_id is not None else current_school_id()
    col = f"{alias}.school_id" if alias else "school_id"
    return (f"{col}=%s", (sid,))


def fetch_school(school_id=None):
    """Charge les infos école (nom, devise, couleur…) pour affichage UI."""
    sid = school_id if school_id is not None else current_school_id()
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM schools WHERE id=%s", (sid,))
        return cur.fetchone()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def list_active_schools():
    """Liste les écoles actives — utile pour le futur superadmin."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, nom, code, statut, devise, pays, ville
            FROM schools WHERE statut IN ('active','trial') ORDER BY nom
        """)
        return cur.fetchall()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def school_exists(school_id):
    """Vérifie qu'une école existe réellement (anti-injection de school_id arbitraire)."""
    if school_id is None:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM schools WHERE id = %s LIMIT 1", (int(school_id),))
        return cur.fetchone() is not None
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _lookup_school_id_by_domain(domain):
    """Retourne l'id d'école dont schools.domaine == domain (insensible à la casse)."""
    if not domain:
        return None
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM schools WHERE LOWER(domaine) = %s LIMIT 1",
            (domain.strip().lower(),),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _lookup_school_id_by_code(code):
    """Retourne l'id d'école dont schools.code == code (insensible à la casse)."""
    if not code:
        return None
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM schools WHERE LOWER(code) = %s LIMIT 1",
            (code.strip().lower(),),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def resolve_school_ref(ref):
    """Résout un school_id à partir d'une référence choisie par l'utilisateur sur
    un formulaire public : identifiant numérique, code d'école ou domaine.
    Retourne un int (validé en base) ou None. Aucune valeur par défaut."""
    if ref is None:
        return None
    ref = str(ref).strip()
    if not ref:
        return None
    if ref.isdigit():
        sid = int(ref)
        return sid if school_exists(sid) else None
    return _lookup_school_id_by_code(ref) or _lookup_school_id_by_domain(ref)


def resolve_school_id_by_email(email):
    """Résout le school_id à partir du domaine de l'email (schools.domaine).
    Retourne un int ou None si aucun domaine ne correspond. Aucune valeur par défaut."""
    if not email or '@' not in email:
        return None
    return _lookup_school_id_by_domain(email.rsplit('@', 1)[-1])


def resolve_school_id_by_host(host):
    """Résout le school_id à partir du host/sous-domaine HTTP (schools.domaine).
    Essaie le host complet puis le domaine parent (ecole.domaine.tld → domaine.tld).
    Retourne un int ou None. Aucune valeur par défaut."""
    if not host:
        return None
    host = host.split(':')[0].strip().lower()  # retire un éventuel port
    sid = _lookup_school_id_by_domain(host)
    if sid:
        return sid
    if '.' in host:
        return _lookup_school_id_by_domain(host.split('.', 1)[1])
    return None


def _only_active_school_id():
    """Retourne l'unique école active s'il n'en existe qu'une (déploiement mono-tenant).
    Retourne None s'il y a 0 ou plusieurs écoles. Jamais de valeur codée en dur."""
    schools = list_active_schools()
    if len(schools) == 1:
        return int(schools[0]['id'])
    return None


def resolve_public_school_id(email=None, explicit_school_id=None, host=None,
                             explicit_school_ref=None):
    """Résolution du tenant pour les routes PUBLIQUES (sans session de confiance).

    Ordre de résolution (priorité décroissante) :
        1. École explicitement choisie par l'utilisateur (identifiant, code ou
           domaine d'école sur le formulaire), validée en base
        2. Domaine de l'email — uniquement comme mécanisme de démonstration /
           secours (@edu.ne, @swissumef.ne …)
        3. Host / sous-domaine HTTP
        4. Unique école active (déploiement mono-tenant)

    Fail-closed : retourne None si l'école ne peut pas être déterminée de façon
    fiable. Aucune valeur par défaut (jamais de school_id=1 implicite). Fonctionne
    pour un nombre illimité d'écoles sans modification du code.

    NB : les routes AUTHENTIFIÉES n'utilisent jamais cette fonction ; elles
    s'appuient exclusivement sur session['school_id'] via current_school_id().
    """
    # 1. Choix explicite de l'école (id numérique, code ou domaine)
    sid = resolve_school_ref(explicit_school_ref)
    if sid:
        return sid
    if explicit_school_id is not None:
        try:
            sid = int(explicit_school_id)
        except (TypeError, ValueError):
            sid = None
        if sid and sid >= 1 and school_exists(sid):
            return sid
    # 2. Domaine de l'email (démonstration / secours)
    sid = resolve_school_id_by_email(email)
    if sid:
        return sid
    # 3. Host / sous-domaine HTTP
    sid = resolve_school_id_by_host(host)
    if sid:
        return sid
    # 4. Unique école active (mono-tenant)
    return _only_active_school_id()


def init_app(flask_app):
    """
    Hooks Flask :
    - Avant chaque requête : pose g.school_id depuis la session (ou défaut)
    - Expose `current_school` (dict) aux templates Jinja
    """
    @flask_app.before_request
    def _set_tenant_context():
        g.school_id = session.get('school_id', DEFAULT_SCHOOL_ID)

    @flask_app.context_processor
    def _inject_school():
        try:
            return {'current_school': fetch_school(), 'current_school_id': current_school_id()}
        except Exception:
            return {'current_school': None, 'current_school_id': DEFAULT_SCHOOL_ID}
