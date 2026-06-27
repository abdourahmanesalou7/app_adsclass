# Diagnostic temporaire - fuite visibilite cours (a supprimer apres usage)
import json
from db import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)


def show(title, sql, params=()):
    print("\n" + "=" * 70)
    print(title)
    print("SQL:", " ".join(sql.split()))
    print("PARAMS:", params)
    cur.execute(sql, params)
    rows = cur.fetchall()
    print("ROWS:", len(rows))
    for r in rows:
        print("  ", r)
    return rows


# 1. Schools
cur.execute("SHOW COLUMNS FROM schools")
print("SCHOOLS COLS:", [c["Field"] for c in cur.fetchall()])
schools = show("SCHOOLS", "SELECT * FROM schools")

# 2. Tous les cours Math (toutes ecoles)
courses = show(
    "COURSES (nom_cours LIKE math)",
    "SELECT id, nom_cours, school_id, filiere, niveau, date_cours, start, end, "
    "jour_semaine, professeur_id FROM courses WHERE nom_cours LIKE %s",
    ("%ath%",),
)

# 3. emploi_temps pour ces cours
if courses:
    ids = [c["id"] for c in courses]
    placeholders = ",".join(["%s"] * len(ids))
    show(
        "EMPLOI_TEMPS pour ces course_id",
        f"SELECT id, user_id, course_id, role, school_id, visible "
        f"FROM emploi_temps WHERE course_id IN ({placeholders})",
        tuple(ids),
    )

# 4. Etudiants edu.ne / swissumef.ne
students = show(
    "STUDENTS (@edu.ne / @swissumef.ne)",
    "SELECT id, email, role, school_id, filiere, niveau FROM users "
    "WHERE role='etudiant' AND (email LIKE %s OR email LIKE %s)",
    ("%@edu.ne", "%@swissumef.ne"),
)

# 5. Pour chaque etudiant, simuler la requete /student/courses
for st in students:
    uid = st["id"]
    sid = st["school_id"]
    print("\n" + "#" * 70)
    print(f"STUDENT id={uid} email={st['email']} school_id={sid} "
          f"filiere={st['filiere']} niveau={st['niveau']}")
    # emploi_temps de cet etudiant
    show(
        "  -> emploi_temps de l'etudiant",
        "SELECT id, course_id, role, school_id, visible FROM emploi_temps "
        "WHERE user_id=%s",
        (uid,),
    )
    # Requete reelle student_courses (JOIN tenant-safe + school filter)
    show(
        "  -> /student/courses (reel)",
        "SELECT c.id, c.nom_cours, c.school_id, c.date_cours, c.start "
        "FROM courses c "
        "JOIN emploi_temps et ON c.id = et.course_id "
        "AND (et.school_id = c.school_id OR et.school_id IS NULL) "
        "WHERE et.user_id=%s AND et.role='etudiant' "
        "AND c.school_id=%s AND (et.school_id=%s OR et.school_id IS NULL)",
        (uid, sid, sid),
    )

conn.close()
print("\nDIAG DONE")
