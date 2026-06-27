# -*- coding: utf-8 -*-
"""Validation post-reset : connexions, isolation des permissions par rôle, multi-tenant."""
from werkzeug.security import check_password_hash
from db import get_db_connection
from permissions import PermissionManager

PWD = "Test@12345"


def main():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # 1) Toutes les connexions : le hash valide-t-il Test@12345 ?
    cur.execute("SELECT id, email, password, must_change_password FROM users WHERE role <> 'superadmin'")
    rows = cur.fetchall()
    bad_pwd = [r["email"] for r in rows if not check_password_hash(r["password"], PWD)]
    bad_flag = [r["email"] for r in rows if r["must_change_password"] != 1]
    print(f"[1] Connexions : {len(rows)} comptes — hash KO: {len(bad_pwd)} — must_change_password!=1: {len(bad_flag)}")
    for e in bad_pwd:
        print("    !! mot de passe invalide:", e)

    # 2) Permissions par rôle : chaque rôle ne porte QUE ses permissions (modèle école 1)
    cur.execute("SELECT id, nom, school_id FROM admin_roles ORDER BY school_id, nom")
    roles = cur.fetchall()
    # modèle = permissions des rôles de l'école 1
    tmpl = {}
    for r in roles:
        if r["school_id"] == 1:
            cur.execute("""
                SELECT p.code FROM admin_role_permissions rp
                JOIN admin_permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = %s
            """, (r["id"],))
            tmpl[r["nom"]] = {x["code"] for x in cur.fetchall()}
    print("[2] Permissions par rôle (comparaison à l'école modèle) :")
    mismatch = 0
    for r in roles:
        cur.execute("""
            SELECT p.code FROM admin_role_permissions rp
            JOIN admin_permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = %s
        """, (r["id"],))
        codes = {x["code"] for x in cur.fetchall()}
        ref = tmpl.get(r["nom"], codes)
        flag = "" if codes == ref else "  <-- DIFF"
        if codes != ref:
            mismatch += 1
        print(f"    [{r['school_id']}] {r['nom']:<26} {len(codes):>2} perms{flag}")

    # 3) Multi-tenant : un Directeur ne voit que les users de son école
    cur.execute("""
        SELECT u.id, u.email, u.school_id FROM users u
        JOIN admin_roles r ON u.admin_role_id = r.id
        WHERE r.nom = 'Directeur' ORDER BY u.school_id
    """)
    dirs = cur.fetchall()
    print("[3] Multi-tenant — permissions effectives par Directeur :")
    for d in dirs:
        perms = PermissionManager.get_user_permissions(d["id"])
        print(f"    {d['email']:<26} school={d['school_id']} perms={len(perms)}")

    cur.close()
    conn.close()
    ok = not bad_pwd and not bad_flag and mismatch == 0
    print("\nRésultat validation :", "OK ✅" if ok else "ÉCHEC ❌")


if __name__ == "__main__":
    main()
