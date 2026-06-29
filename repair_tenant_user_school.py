"""Réparation des données multi-tenant corrompues.

Aligne users.school_id sur l'école déduite du domaine de l'email
(schools.domaine) et reconstruit les inscriptions emploi_temps dans la
bonne école. Idempotent et re-exécutable.

Usage :
    venv\\Scripts\\python.exe repair_tenant_user_school.py            # applique
    venv\\Scripts\\python.exe repair_tenant_user_school.py --dry-run  # simulation
"""
import sys

from db import get_db_connection
from tenant import resolve_school_id_by_email
from student_enrollment_service import sync_enrollments_for_student


def repair(dry_run=False):
    conn = get_db_connection()
    if not conn:
        print("ERREUR: connexion DB indisponible")
        return 1
    cur = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT id, email, role, school_id, filiere, niveau FROM users "
        "WHERE email IS NOT NULL AND email <> ''"
    )
    users = cur.fetchall()

    fixed = []
    for u in users:
        expected = resolve_school_id_by_email(u['email'])
        # On ne corrige que si le domaine correspond à une école connue
        # ET diffère du school_id stocké. Emails @adsclass.ne (aucune école)
        # sont ignorés -> laissés tels quels.
        if expected is None:
            continue
        if int(u['school_id'] or 0) == expected:
            continue
        fixed.append({
            'id': u['id'], 'email': u['email'], 'role': u['role'],
            'old': u['school_id'], 'new': expected,
        })

    if not fixed:
        print("Aucune incohérence school_id/domaine détectée. RAS.")
        conn.close()
        return 0

    print(f"{len(fixed)} utilisateur(s) à corriger :")
    for f in fixed:
        print(f"  - id={f['id']:>4} {f['email']:<32} role={f['role']:<11} "
              f"school_id {f['old']} -> {f['new']}")

    if dry_run:
        print("\n[DRY-RUN] aucune modification appliquée.")
        conn.close()
        return 0

    for f in fixed:
        uid, new_sid = f['id'], f['new']
        # 1. Corriger l'école de l'utilisateur
        cur.execute(
            "UPDATE users SET school_id = %s WHERE id = %s", (new_sid, uid)
        )
        # 2. Réaligner la table de profil correspondante si elle existe
        for tbl in ('etudiants', 'professeurs', 'administrateurs'):
            try:
                cur.execute(f"SHOW COLUMNS FROM {tbl} LIKE 'school_id'")
                if cur.fetchone():
                    cur.execute(
                        f"UPDATE {tbl} SET school_id = %s WHERE user_id = %s",
                        (new_sid, uid),
                    )
            except Exception:
                pass
        # 3. Reconstruire les inscriptions emploi_temps (étudiants uniquement)
        if f['role'] == 'etudiant':
            cur.execute(
                "DELETE FROM emploi_temps WHERE user_id = %s AND role = 'etudiant'",
                (uid,),
            )
            try:
                sync_enrollments_for_student(cur, uid, new_sid)
            except Exception as e:
                print(f"    ! sync inscriptions échouée pour id={uid}: {e}")

    conn.commit()
    print(f"\nOK: {len(fixed)} utilisateur(s) corrigé(s) et inscriptions resynchronisées.")
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(repair(dry_run='--dry-run' in sys.argv))
