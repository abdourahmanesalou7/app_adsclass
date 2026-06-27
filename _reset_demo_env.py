# -*- coding: utf-8 -*-
"""Réinitialisation idempotente de l'environnement de démonstration RBAC multi-tenant.

- Ne modifie ni la logique métier, ni les URLs, ni le schéma SQL.
- Nettoie les données de démo dans l'ordre des dépendances (FK-safe).
- Configure les domaines des écoles, réplique les rôles système par école,
  recrée les comptes (staff / enseignants / étudiants) + profils + RBAC.
- Rejouable sans effet de bord (purge puis recrée).
"""
from datetime import date
from db import get_db_connection

try:
    from werkzeug.security import generate_password_hash as _wz_hash
except Exception:  # pragma: no cover
    _wz_hash = None

DEMO_PASSWORD = "Test@12345"

# École -> domaine (le user impose École 1 => edu.ne, École 2 => swissumef.ne)
DOMAINS = {1: "edu.ne", 2: "swissumef.ne"}

# Comptes "staff" administratifs : (local_email, nom_du_role, prenom, fonction/service)
STAFF = [
    ("directeur",   "Directeur",                 "Directeur",   "Direction"),
    ("admissions",  "Responsable Admissions",    "Responsable", "Admissions"),
    ("pedagogie",   "Responsable Pédagogique",   "Responsable", "Pédagogie"),
    ("secretaire",  "Secrétaire",                "Secrétaire",  "Secrétariat"),
    ("comptable",   "Comptable",                 "Comptable",   "Comptabilité"),
    ("surveillant", "Surveillant",               "Surveillant", "Surveillance"),
]

# Ordre de suppression : enfants -> parents (intégrité référentielle préservée).
WIPE_ORDER = [
    "admissions_communications", "admissions_documents", "admissions_entretiens",
    "admissions_historique", "admissions_paiements",
    "attestations_blockchain", "attestations_historique",
    "assignment_submissions",
    "gradebook", "exams", "emploi_temps", "documents", "presences",
    "absences", "notes", "paiements", "presences_generales",
    "administrators_profiles", "professors_profiles", "students_profiles",
    "assignments", "demandes_attestations", "admissions_candidats",
    "courses",
]


def hash_pwd(plain):
    if _wz_hash is None:
        raise RuntimeError("werkzeug indisponible")
    try:
        return _wz_hash(str(plain))
    except (ValueError, OSError):
        return _wz_hash(str(plain), method="pbkdf2:sha256")


def prefix_for(school_id):
    """Préfixe identifiants/matricules dérivé du domaine (edu -> EDU)."""
    dom = DOMAINS.get(school_id, f"school{school_id}.demo.ne")
    return dom.split(".")[0].upper()


def set_school_domains(cur):
    for sid, dom in DOMAINS.items():
        cur.execute("SELECT id FROM schools WHERE id = %s", (sid,))
        if cur.fetchone():
            cur.execute("UPDATE schools SET domaine = %s WHERE id = %s", (dom, sid))
            print(f"  école {sid} -> domaine {dom}")


def list_schools(cur):
    cur.execute("SELECT id, nom, code FROM schools ORDER BY id")
    return cur.fetchall()


def wipe_demo_data(cur):
    """Supprime toutes les données de démo (conserve schools, rôles, permissions,
    filières, modules, années, abonnements). Les superadmin sont préservés."""
    for tbl in WIPE_ORDER:
        cur.execute(f"DELETE FROM {tbl}")
        print(f"  purge {tbl}: {cur.rowcount} ligne(s)")
    # Comptes : on garde uniquement les superadmin (rôle plateforme)
    cur.execute("DELETE FROM users WHERE role <> 'superadmin'")
    print(f"  purge users (non-superadmin): {cur.rowcount} ligne(s)")


def ensure_roles_per_school(cur):
    """Réplique les rôles (et leurs permissions) de l'école modèle (school_id=1)
    vers toutes les autres écoles. Idempotent via (school_id, nom)."""
    cur.execute("""
        SELECT id, nom, description, couleur, icone, priorite, actif, is_system
        FROM admin_roles WHERE school_id = 1
    """)
    template_roles = cur.fetchall()
    schools = [s["id"] for s in list_schools(cur)]
    for sid in schools:
        if sid == 1:
            continue
        for r in template_roles:
            cur.execute(
                "SELECT id FROM admin_roles WHERE school_id = %s AND nom = %s",
                (sid, r["nom"]),
            )
            existing = cur.fetchone()
            if existing:
                new_id = existing["id"]
            else:
                cur.execute("""
                    INSERT INTO admin_roles
                        (nom, description, couleur, icone, priorite, actif, school_id, is_system)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (r["nom"], r["description"], r["couleur"], r["icone"],
                      r["priorite"], r["actif"], sid, r["is_system"]))
                new_id = cur.lastrowid
                print(f"  rôle '{r['nom']}' créé pour école {sid} (id={new_id})")
            # (Re)synchronise les permissions depuis le rôle modèle
            cur.execute(
                "SELECT permission_id FROM admin_role_permissions WHERE role_id = %s",
                (r["id"],),
            )
            wanted = {row["permission_id"] for row in cur.fetchall()}
            cur.execute(
                "SELECT permission_id FROM admin_role_permissions WHERE role_id = %s",
                (new_id,),
            )
            current = {row["permission_id"] for row in cur.fetchall()}
            for pid in wanted - current:
                cur.execute(
                    "INSERT INTO admin_role_permissions (role_id, permission_id) VALUES (%s,%s)",
                    (new_id, pid),
                )


def role_map(cur):
    """{ (school_id, nom_role) : role_id }"""
    cur.execute("SELECT id, school_id, nom FROM admin_roles")
    return {(r["school_id"], r["nom"]): r["id"] for r in cur.fetchall()}


def _insert_user(cur, school_id, nom, prenom, email, role, admin_role_id, ident):
    pwd = hash_pwd(DEMO_PASSWORD)
    cur.execute("""
        INSERT INTO users
            (nom, prenom, email, password, role, admin_role_id, identifiant,
             password_temp, must_change_password, school_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
    """, (nom, prenom, email, pwd, role, admin_role_id, ident,
          DEMO_PASSWORD, school_id))
    return cur.lastrowid


def seed_school(cur, school, rmap, created):
    sid = school["id"]
    dom = DOMAINS.get(sid)
    if not dom:
        return
    pfx = prefix_for(sid)
    nom_ecole = school["nom"]

    # --- Staff administratifs (role users = 'admin' + admin_role_id ciblé) ---
    for i, (local, role_name, prenom, fonction) in enumerate(STAFF, start=1):
        arid = rmap.get((sid, role_name))
        ident = f"{pfx}-ADM-{i:03d}"
        uid = _insert_user(cur, sid, pfx, role_name,
                           f"{local}@{dom}", "admin", arid, ident)
        cur.execute("""
            INSERT INTO administrators_profiles (user_id, matricule, service, fonction, statut)
            VALUES (%s,%s,%s,%s,'actif')
        """, (uid, ident, fonction, role_name))
        created.append((nom_ecole, f"{role_name} {pfx}", f"{local}@{dom}", role_name))

    # --- 2 Enseignants ---
    for i in range(1, 3):
        ident = f"{pfx}-PROF-{i:03d}"
        uid = _insert_user(cur, sid, pfx, f"Professeur {i}",
                           f"professeur{i}@{dom}", "professeur", None, ident)
        cur.execute("""
            INSERT INTO professors_profiles (user_id, matricule, departement, statut)
            VALUES (%s,%s,%s,'actif')
        """, (uid, ident, "Général"))
        created.append((nom_ecole, f"Professeur {i} {pfx}",
                        f"professeur{i}@{dom}", "Enseignant"))

    # --- 10 Étudiants ---
    for i in range(1, 11):
        ident = f"{pfx}-ETU-{i:03d}"
        uid = _insert_user(cur, sid, pfx, f"Étudiant {i}",
                           f"etudiant{i}@{dom}", "etudiant", None, ident)
        cur.execute("""
            INSERT INTO students_profiles (user_id, matricule, date_inscription, statut_inscription)
            VALUES (%s,%s,%s,'actif')
        """, (uid, ident, date.today()))
        created.append((nom_ecole, f"Étudiant {i} {pfx}",
                        f"etudiant{i}@{dom}", "Étudiant"))


def validate(cur):
    print("\n=== VALIDATION ===")
    # 1) Aucun utilisateur ne référence un rôle d'une autre école
    cur.execute("""
        SELECT u.id, u.email, u.school_id AS u_sid, r.school_id AS r_sid, r.nom
        FROM users u JOIN admin_roles r ON u.admin_role_id = r.id
        WHERE u.school_id <> r.school_id
    """)
    leaks = cur.fetchall()
    print(f"  fuites cross-tenant (user.school_id <> role.school_id) : {len(leaks)}")
    for l in leaks:
        print(f"    !! {l['email']} user_sid={l['u_sid']} role_sid={l['r_sid']} ({l['nom']})")

    # 2) Répartition des comptes
    cur.execute("""
        SELECT school_id, role, COUNT(*) n FROM users
        GROUP BY school_id, role ORDER BY school_id, role
    """)
    print("  répartition users (school_id, role, n):")
    for r in cur.fetchall():
        print(f"    {r['school_id']:>2} | {r['role']:<11} | {r['n']}")
    return len(leaks) == 0


def print_deliverable(created):
    print("\n=== COMPTES DE DÉMONSTRATION (mot de passe commun) ===")
    head = f"| {'École':<22} | {'Nom':<26} | {'Email':<28} | {'Rôle':<24} | {'Mot de passe':<12} |"
    sep = "|" + "-" * (len(head) - 2) + "|"
    print(sep); print(head); print(sep)
    for ecole, nom, email, role in created:
        print(f"| {ecole[:22]:<22} | {nom[:26]:<26} | {email[:28]:<28} | {role[:24]:<24} | {DEMO_PASSWORD:<12} |")
    print(sep)
    print(f"Total comptes créés : {len(created)}  (must_change_password = 1)")


def main():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        print("=== 1. Domaines des écoles ==="); set_school_domains(cur)
        print("=== 2. Purge des données de démonstration (ordre FK-safe) ==="); wipe_demo_data(cur)
        print("=== 3. Réplication des rôles système par école ==="); ensure_roles_per_school(cur)
        conn.commit()

        rmap = role_map(cur)
        created = []
        print("=== 4. Création des comptes + profils ===")
        for school in list_schools(cur):
            if school["id"] in DOMAINS:
                seed_school(cur, school, rmap, created)
        conn.commit()

        ok = validate(cur)
        print_deliverable(created)
        print("\nRésultat :", "OK ✅" if ok else "ÉCHEC ❌ (fuite cross-tenant)")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
