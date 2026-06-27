"""Provisionnement des comptes d'administration d'école (Directeur, etc.).

Crée le compte du Directeur (ou autre rôle admin) d'une école avec :
- email professionnel (fourni ou dérivé du domaine : directeur@<domaine>)
- identifiant unique, profil administrateur, rôle RBAC scoped school_id
- mot de passe temporaire (must_change_password = 1)

Les rôles RBAC de l'école sont garantis via ensure_roles_for_school().
"""
import re
import unicodedata
from werkzeug.security import generate_password_hash as _wz_hash

from db import get_db_connection
from init_rbac_multitenant import ensure_roles_for_school

DEFAULT_PASSWORD = "Test@12345"


def _hash(pw):
    """Hash tolérant (fallback pbkdf2 si scrypt indisponible hors-process)."""
    try:
        return _wz_hash(str(pw))
    except (ValueError, OSError):
        return _wz_hash(str(pw), method="pbkdf2:sha256")


def _slug(text):
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", norm)


def _prefix(school):
    """Préfixe identifiants/matricules dérivé du domaine puis du code."""
    dom = (school.get("domaine") or "").strip()
    if dom:
        return dom.split(".")[0].upper()
    code = (school.get("code") or "ECOLE").strip().upper()
    return re.sub(r"[^A-Z0-9]", "", code)[:8] or "ECOLE"


def derive_director_email(school):
    """directeur@<domaine> si l'école a un domaine, sinon None."""
    dom = (school.get("domaine") or "").strip().lower()
    return f"directeur@{dom}" if dom else None


def _unique_identifiant(cur, prefix, kind="DIR"):
    base = f"{prefix}-{kind}-"
    cur.execute(
        "SELECT identifiant FROM users WHERE identifiant LIKE %s ORDER BY identifiant DESC LIMIT 1",
        (base + "%",),
    )
    row = cur.fetchone()
    seq = 1
    if row and row.get("identifiant"):
        try:
            seq = int(str(row["identifiant"]).split("-")[-1]) + 1
        except ValueError:
            seq = 1
    return f"{base}{seq:03d}"


def create_school_director(school, email=None, prenom=None, nom=None,
                           password=None, role_name="Directeur"):
    """Crée un compte admin (Directeur par défaut) pour `school` (dict).

    Retourne (credentials_dict, None) en cas de succès, sinon (None, message_erreur).
    """
    school_id = school["id"]
    ensure_roles_for_school(school_id)

    pwd_plain = password or DEFAULT_PASSWORD
    email = (email or derive_director_email(school) or "").strip().lower()
    if not email:
        return None, "Email du directeur requis (aucun domaine défini sur l'école)."

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM admin_roles WHERE school_id=%s AND nom=%s",
                    (school_id, role_name))
        role = cur.fetchone()
        if not role:
            return None, f"Rôle « {role_name} » introuvable pour cette école."

        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return None, f"L'email {email} est déjà utilisé."

        prefix = _prefix(school)
        ident = _unique_identifiant(cur, prefix)
        prenom = (prenom or "").strip() or role_name
        nom = (nom or "").strip() or prefix

        cur.execute("""
            INSERT INTO users
                (nom, prenom, email, password, role, admin_role_id, identifiant,
                 password_temp, must_change_password, school_id)
            VALUES (%s,%s,%s,%s,'admin',%s,%s,%s,1,%s)
        """, (nom, prenom, email, _hash(pwd_plain), role["id"], ident,
              pwd_plain, school_id))
        uid = cur.lastrowid

        cur.execute("""
            INSERT INTO administrators_profiles
                (user_id, matricule, service, fonction, statut)
            VALUES (%s,%s,'Direction',%s,'actif')
        """, (uid, ident, role_name))
        conn.commit()
        return ({"user_id": uid, "email": email, "identifiant": ident,
                 "password": pwd_plain, "role": role_name,
                 "nom": nom, "prenom": prenom}, None)
    except Exception as e:
        conn.rollback()
        return None, f"Erreur création directeur : {e}"
    finally:
        cur.close()
        conn.close()


def reset_user_password(user_id, password=None):
    """Réinitialise le mot de passe d'un compte (temp + must_change_password=1)."""
    pwd = password or DEFAULT_PASSWORD
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET password=%s, password_temp=%s, must_change_password=1 WHERE id=%s",
            (_hash(pwd), pwd, user_id))
        conn.commit()
        return pwd if cur.rowcount > 0 else None
    finally:
        cur.close()
        conn.close()
