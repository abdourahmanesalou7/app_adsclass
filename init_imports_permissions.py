"""
Crée les permissions RBAC du module Import et les attribue aux rôles existants.
Idempotent. Exécuter : python init_imports_permissions.py
"""
from db import get_db_connection

PERMISSIONS = [
    ('imports.view',    'Voir les imports',     'Consulter l\'historique et les détails des imports', 'imports'),
    ('imports.execute', 'Exécuter un import',   'Créer, mapper, valider et committer un import',      'imports'),
    ('imports.delete',  'Supprimer un import',  'Supprimer un job d\'import et son staging',          'imports'),
]

ROLE_GRANTS = {
    'Super Administrateur': ['imports.view', 'imports.execute', 'imports.delete'],
    'Directeur':            ['imports.view', 'imports.execute'],
    'Secrétaire':           ['imports.view'],
}


def run():
    conn = get_db_connection()
    if not conn:
        print("❌ Connexion impossible")
        return 1
    cur = conn.cursor()
    try:
        # 1. Insertion des permissions
        for code, nom, desc, module in PERMISSIONS:
            cur.execute("""
                INSERT IGNORE INTO admin_permissions (code, nom, description, module)
                VALUES (%s, %s, %s, %s)
            """, (code, nom, desc, module))
        conn.commit()

        # 2. Récupération IDs
        cur.execute("SELECT id, code FROM admin_permissions WHERE module='imports'")
        perm_map = {code: pid for pid, code in cur.fetchall()}
        cur.execute("SELECT id, nom FROM admin_roles")
        role_map = {nom: rid for rid, nom in cur.fetchall()}

        # 3. Attribution
        grants = 0
        for role_name, codes in ROLE_GRANTS.items():
            if role_name not in role_map:
                print(f"⚠ Rôle '{role_name}' absent — skip")
                continue
            rid = role_map[role_name]
            for code in codes:
                if code in perm_map:
                    cur.execute("""
                        INSERT IGNORE INTO admin_role_permissions (role_id, permission_id)
                        VALUES (%s, %s)
                    """, (rid, perm_map[code]))
                    grants += 1
        conn.commit()
        print(f"✅ {len(PERMISSIONS)} permission(s) imports + {grants} attribution(s) OK")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ {e}")
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
