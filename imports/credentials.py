"""
Génération automatique d'identifiants pour les imports d'utilisateurs.
Aligné sur le pipeline admissions (student_enrollment_service).
"""
import re
from werkzeug.security import generate_password_hash as _werkzeug_hash


def _hash_password(plain):
    """Wrapper tolérant : scrypt peut échouer (malloc) sur Windows en hors-process Flask.
    Fallback transparent sur pbkdf2 (rapide, compatible OpenSSL standard)."""
    try:
        return _werkzeug_hash(str(plain))
    except (ValueError, OSError):
        return _werkzeug_hash(str(plain), method='pbkdf2:sha256')

from student_enrollment_service import (
    ensure_student_account_columns,
    generer_email_etudiant,
    generer_identifiant,
    generer_mot_de_passe,
    generer_nom_classe,
    normaliser_niveau,
    NIVEAU_SHORT_TO_LONG,
    sync_enrollments_for_student,
)


ROLE_BY_SCHEMA = {
    'students': 'etudiant',
    'professeurs': 'professeur',
    'administrateurs': 'admin',
}

PREFIX_BY_ROLE = {
    'professeur': 'PROF',
    'admin': 'ADM',
}


def _resolve_filiere(cur, filiere_name):
    if not filiere_name:
        return None
    cur.execute(
        "SELECT id, nom, code, niveau FROM filieres WHERE nom = %s AND est_active = 1",
        (filiere_name,),
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        "SELECT id, nom, code, niveau FROM filieres WHERE code = %s AND est_active = 1",
        (str(filiere_name).upper(),),
    )
    return cur.fetchone()


def _generer_identifiant_staff(cur, role):
    """Identifiant simple pour professeur/admin : PROF-2026-0001 / ADM-2026-0001."""
    from datetime import datetime
    prefix = f"{PREFIX_BY_ROLE.get(role, 'USR')}-{datetime.now().year}-"
    cur.execute(
        "SELECT identifiant FROM users WHERE identifiant LIKE %s ORDER BY identifiant DESC LIMIT 1",
        (prefix + '%',),
    )
    row = cur.fetchone()
    seq = 1
    if row and row.get('identifiant'):
        try:
            seq = int(str(row['identifiant']).split('-')[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


EMAIL_DOMAIN = 'adsclass.ne'


def _slugify_email(text):
    """Slug ASCII pour partie locale d'email : minuscules, sans accent, sans séparateur."""
    import unicodedata
    if not text:
        return ''
    norm = unicodedata.normalize('NFKD', str(text))
    ascii_only = norm.encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]', '', ascii_only)


def _generer_email_pro(cur, prenom, nom):
    """Génère un email professionnel `prenom.nom@adsclass.ne` unique.
    Gère les collisions par suffixe incrémental (`.2`, `.3`, …)."""
    base = f"{_slugify_email(prenom)}.{_slugify_email(nom)}".strip('.')
    if not base or base == '.':
        base = 'utilisateur'
    candidate = f"{base}@{EMAIL_DOMAIN}"
    suffix = 2
    while True:
        cur.execute("SELECT 1 FROM users WHERE email = %s LIMIT 1", (candidate,))
        if not cur.fetchone():
            return candidate
        candidate = f"{base}.{suffix}@{EMAIL_DOMAIN}"
        suffix += 1
        if suffix > 999:
            import secrets
            return f"{base}.{secrets.token_hex(3)}@{EMAIL_DOMAIN}"


def enrich_user_payload(cur, schema_key, users_data, profile_data, force_pro_email=False):
    """
    Complète les champs manquants AVANT l'INSERT :
      - email auto (prenom.nom@adsclass.ne) — toujours regénéré si force_pro_email=True
      - password_temp + password (hash)
      - identifiant (étudiants : IA-M2-2026-0001 ; staff : PROF/ADM-2026-0001)
      - filiere_id, classe, niveau canonique (étudiants)
      - must_change_password = 1
    Retourne (users_data_enrichi, profile_data_enrichi, credentials_dict_pour_print).
    """
    ensure_student_account_columns(cur)
    role = ROLE_BY_SCHEMA.get(schema_key) or users_data.get('role') or 'etudiant'
    users_data['role'] = role

    nom = (users_data.get('nom') or '').strip()
    prenom = (users_data.get('prenom') or '').strip()

    # Email : en mode import (force_pro_email=True) on normalise toujours sur le domaine
    # institutionnel, même si la source fournit un email perso. Sinon (création manuelle),
    # on conserve l'email saisi par l'admin et on ne génère que s'il est absent.
    email = (users_data.get('email') or '').strip().lower()
    if force_pro_email or not email:
        email = _generer_email_pro(cur, prenom, nom)
    users_data['email'] = email

    # Mot de passe temporaire (toujours regénéré si absent)
    password_plain = users_data.get('password')
    if not password_plain:
        password_plain = generer_mot_de_passe()
    users_data['password'] = _hash_password(password_plain)
    users_data['password_temp'] = password_plain
    users_data['must_change_password'] = 1

    filiere_nom = (users_data.get('filiere') or '').strip() or None
    niveau_in = (users_data.get('niveau') or '').strip() or None
    niveau_short = normaliser_niveau(niveau_in) if niveau_in else None

    if role == 'etudiant':
        filiere_row = _resolve_filiere(cur, filiere_nom)
        filiere_code = (filiere_row or {}).get('code') or (filiere_nom or 'ADS')
        if filiere_row:
            users_data['filiere_id'] = filiere_row['id']
            users_data['filiere'] = filiere_row['nom']
        niv_short_eff = niveau_short or normaliser_niveau((filiere_row or {}).get('niveau'))
        users_data['niveau'] = NIVEAU_SHORT_TO_LONG.get(niv_short_eff, niv_short_eff)
        if not (users_data.get('classe') or '').strip():
            users_data['classe'] = generer_nom_classe(niv_short_eff, users_data.get('filiere'), filiere_code)
        if not (users_data.get('identifiant') or '').strip():
            users_data['identifiant'] = generer_identifiant(cur, filiere_code, niv_short_eff)
    else:
        if not (users_data.get('identifiant') or '').strip():
            users_data['identifiant'] = _generer_identifiant_staff(cur, role)
        if niveau_short:
            users_data['niveau'] = NIVEAU_SHORT_TO_LONG.get(niveau_short, niveau_short)

    credentials = {
        'email': users_data['email'],
        'identifiant': users_data.get('identifiant'),
        'password_temp': password_plain,
        'role': role,
        'nom': nom,
        'prenom': prenom,
        'filiere': users_data.get('filiere'),
        'niveau': users_data.get('niveau'),
        'classe': users_data.get('classe'),
    }
    return users_data, profile_data, credentials


def post_enroll_student(cur, user_pk, users_data):
    """Événement métier : inscrit l'étudiant nouvellement créé aux cours
    existants de sa filière/niveau (best-effort). Délègue à la synchro
    centralisée qui lit le school_id depuis la ligne users insérée, garantissant
    l'isolation multi-tenant (aucune inscription cross-école)."""
    try:
        sync_enrollments_for_student(cur, user_pk)
    except Exception:
        pass
