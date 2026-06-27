"""CLI idempotent pour garantir le compte superadmin de la plateforme.

Usage :
    venv\\Scripts\\python.exe manage_superadmin.py [email] [password]

Sans argument : email=super@adsclass.ne, password=Test@12345.
Crée le compte s'il n'existe pas, sinon réinitialise son mot de passe et
garantit role='superadmin'. Le superadmin n'est pas forcé de changer son MDP.
"""
import sys
from werkzeug.security import generate_password_hash

from db import get_db_connection

DEFAULT_EMAIL = "super@adsclass.ne"
DEFAULT_PASSWORD = "Test@12345"


def _hash(pw):
    try:
        return generate_password_hash(str(pw))
    except (ValueError, OSError):
        return generate_password_hash(str(pw), method="pbkdf2:sha256")


def ensure_superadmin(email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD,
                      nom="Super", prenom="Admin"):
    """Crée ou met à jour le compte superadmin. Retourne (email, password)."""
    email = email.strip().lower()
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE users SET password=%s, role='superadmin', "
                "must_change_password=0 WHERE id=%s",
                (_hash(password), row["id"]))
            action = "mis à jour"
        else:
            cur.execute(
                "INSERT INTO users (nom, prenom, email, password, role, "
                "must_change_password, school_id) "
                "VALUES (%s,%s,%s,%s,'superadmin',0,1)",
                (nom, prenom, email, _hash(password)))
            action = "créé"
        conn.commit()
        print(f"✅ Superadmin {action} : {email} / {password}")
        return email, password
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    arg_email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL
    arg_pwd = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PASSWORD
    ensure_superadmin(arg_email, arg_pwd)
