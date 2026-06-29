import app
from app import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

print("=== courses columns ===")
cur.execute("SHOW COLUMNS FROM courses")
cols = [r['Field'] for r in cur.fetchall()]
print(cols)

print("\n=== professeurs ===")
cur.execute("SELECT id, prenom, nom, school_id FROM users WHERE role='professeur' ORDER BY school_id, id")
profs = cur.fetchall()
for p in profs:
    print(p)

print("\n=== courses (prof link state) ===")
cur.execute("""
    SELECT id, nom_cours, professeur_id, professeur_nom, filiere, niveau, school_id, annee_academique_id
    FROM courses ORDER BY school_id, id
""")
for c in cur.fetchall():
    print(c)

print("\n=== emploi_temps (role=professeur) ===")
cur.execute("""
    SELECT et.user_id, et.course_id, et.role, et.school_id AS et_school, et.visible,
           c.school_id AS course_school, c.professeur_id
    FROM emploi_temps et
    LEFT JOIN courses c ON c.id = et.course_id
    WHERE et.role='professeur' ORDER BY et.user_id
""")
for r in cur.fetchall():
    print(r)

print("\n=== mismatch: courses with professeur_id but NO emploi_temps prof row ===")
cur.execute("""
    SELECT c.id, c.nom_cours, c.professeur_id, c.school_id
    FROM courses c
    LEFT JOIN emploi_temps et ON et.course_id=c.id AND et.user_id=c.professeur_id AND et.role='professeur'
    WHERE c.professeur_id IS NOT NULL AND et.user_id IS NULL
""")
miss = cur.fetchall()
for r in miss:
    print(r)
print(f"-> {len(miss)} course(s) missing emploi_temps prof row")

print("\n=== courses with NULL school_id ===")
cur.execute("SELECT COUNT(*) AS n FROM courses WHERE school_id IS NULL")
print(cur.fetchone())

conn.close()
