"""
Migration RBAC multi-tenant — ADSClass.
Idempotent : sûr à ré-exécuter.

Rend les rôles administratifs spécifiques à chaque école (school_id) et
protège les rôles « système » contre la suppression (is_system).

- admin_roles            : + school_id, + is_system, unicité (school_id, nom)
- admin_permissions      : catalogue global inchangé (partagé par toutes les écoles)
- admin_role_permissions : portée héritée via role_id (l'école est implicite)

Aucune donnée n'est supprimée ni déplacée.
"""
from db import get_db_connection, DB_CONFIG

# Rôles fournis par défaut → marqués « système » (non supprimables)
SYSTEM_ROLE_NAMES = [
    'Super Administrateur', 'Directeur', 'Secrétaire',
    'Comptable', 'Surveillant', 'Responsable Admissions',
]


def _column_exists(cur, table, column):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (DB_CONFIG['database'], table, column))
    return cur.fetchone()[0] > 0


def _index_exists(cur, table, index_name):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s
    """, (DB_CONFIG['database'], table, index_name))
    return cur.fetchone()[0] > 0


def _table_exists(cur, table):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
    """, (DB_CONFIG['database'], table))
    return cur.fetchone()[0] > 0


def ensure_rbac_multitenant():
    """Applique la migration RBAC multi-tenant. Idempotent."""
    conn = get_db_connection()
    if not conn:
        print("⚠️ RBAC multi-tenant : connexion DB impossible")
        return False
    cur = conn.cursor()
    try:
        # admin_roles absente → rien à migrer (init_roles.py la créera plus tard)
        if not _table_exists(cur, 'admin_roles'):
            return True

        # 1. Colonne school_id (+ index)
        if not _column_exists(cur, 'admin_roles', 'school_id'):
            cur.execute("ALTER TABLE admin_roles ADD COLUMN school_id INT NOT NULL DEFAULT 1")
        if not _index_exists(cur, 'admin_roles', 'idx_admin_roles_school'):
            cur.execute("CREATE INDEX idx_admin_roles_school ON admin_roles (school_id)")

        # 2. Colonne is_system
        if not _column_exists(cur, 'admin_roles', 'is_system'):
            cur.execute("ALTER TABLE admin_roles ADD COLUMN is_system TINYINT(1) NOT NULL DEFAULT 0")

        # 3. Unicité globale sur `nom` → unicité par (school_id, nom)
        if _index_exists(cur, 'admin_roles', 'nom'):
            cur.execute("ALTER TABLE admin_roles DROP INDEX nom")
        if not _index_exists(cur, 'admin_roles', 'uq_role_school_nom'):
            cur.execute("ALTER TABLE admin_roles ADD UNIQUE KEY uq_role_school_nom (school_id, nom)")

        # 4. Rattacher l'existant à l'école par défaut
        cur.execute("UPDATE admin_roles SET school_id = 1 WHERE school_id IS NULL OR school_id = 0")

        # 5. Marquer les rôles fournis comme rôles système
        fmt = ','.join(['%s'] * len(SYSTEM_ROLE_NAMES))
        cur.execute(
            f"UPDATE admin_roles SET is_system = 1 WHERE nom IN ({fmt})",
            SYSTEM_ROLE_NAMES,
        )

        conn.commit()
        print("✅ RBAC multi-tenant : rôles spécifiques par école prêts")
        return True
    except Exception as e:
        conn.rollback()
        print(f"⚠️ RBAC multi-tenant : {e}")
        return False
    finally:
        cur.close()
        conn.close()


def ensure_roles_for_school(school_id, template_school_id=1):
    """Réplique les rôles (et leurs permissions) de l'école modèle vers `school_id`.

    Idempotent via la clé (school_id, nom). Utilisé lors de la création d'une
    nouvelle école par le superadmin afin qu'elle dispose immédiatement de tout
    le jeu de rôles RBAC (Directeur, Comptable, …). Retourne le nb de rôles créés.
    """
    if int(school_id) == int(template_school_id):
        return 0
    conn = get_db_connection()
    if not conn:
        return 0
    cur = conn.cursor(dictionary=True)
    created = 0
    try:
        cur.execute("""
            SELECT id, nom, description, couleur, icone, priorite, actif, is_system
            FROM admin_roles WHERE school_id = %s
        """, (template_school_id,))
        templates = cur.fetchall()
        for r in templates:
            cur.execute("SELECT id FROM admin_roles WHERE school_id=%s AND nom=%s",
                        (school_id, r['nom']))
            row = cur.fetchone()
            if row:
                new_id = row['id']
            else:
                cur.execute("""
                    INSERT INTO admin_roles
                        (nom, description, couleur, icone, priorite, actif, school_id, is_system)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (r['nom'], r['description'], r['couleur'], r['icone'],
                      r['priorite'], r['actif'], school_id, r['is_system']))
                new_id = cur.lastrowid
                created += 1
            cur.execute("SELECT permission_id FROM admin_role_permissions WHERE role_id=%s", (r['id'],))
            wanted = {x['permission_id'] for x in cur.fetchall()}
            cur.execute("SELECT permission_id FROM admin_role_permissions WHERE role_id=%s", (new_id,))
            current = {x['permission_id'] for x in cur.fetchall()}
            for pid in wanted - current:
                cur.execute(
                    "INSERT INTO admin_role_permissions (role_id, permission_id) VALUES (%s,%s)",
                    (new_id, pid))
        conn.commit()
        return created
    except Exception as e:
        conn.rollback()
        print(f"⚠️ ensure_roles_for_school({school_id}) : {e}")
        return created
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    ensure_rbac_multitenant()
