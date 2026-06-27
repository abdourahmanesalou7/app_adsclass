"""
Init Superadmin ADSClass — Phase 5
Idempotent : sûr à ré-exécuter.

Crée un utilisateur dédié au pilotage cross-tenant :
  email      : super@adsclass.ne
  password   : super123 (à changer au 1er login)
  role       : 'superadmin'
  school_id  : 1 (rattaché à l'École par défaut, mais bypass total côté middleware)
"""
from werkzeug.security import generate_password_hash
from db import get_db_connection

SUPER_EMAIL = 'super@adsclass.ne'
SUPER_PASSWORD = 'super123'
SUPER_NOM = 'Super'
SUPER_PRENOM = 'Admin'


def main():
    conn = get_db_connection()
    if not conn:
        print("❌ Connexion DB impossible")
        return 1
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, role FROM users WHERE email=%s", (SUPER_EMAIL,))
        row = cur.fetchone()
        if row:
            if row['role'] != 'superadmin':
                cur.execute("UPDATE users SET role='superadmin' WHERE id=%s", (row['id'],))
                conn.commit()
                print(f"✅ Utilisateur {SUPER_EMAIL} promu superadmin (id={row['id']})")
            else:
                print(f"ℹ Superadmin déjà présent : {SUPER_EMAIL} (id={row['id']})")
            return 0

        hashed = generate_password_hash(SUPER_PASSWORD)
        cur.execute("""
            INSERT INTO users
              (nom, prenom, email, password, role, school_id, must_change_password)
            VALUES (%s, %s, %s, %s, 'superadmin', 1, 1)
        """, (SUPER_NOM, SUPER_PRENOM, SUPER_EMAIL, hashed))
        conn.commit()
        print(f"✅ Superadmin créé : {SUPER_EMAIL} / {SUPER_PASSWORD}")
        print("   ⚠ Changez le mot de passe au premier login.")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
