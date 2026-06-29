"""Diagnostic: inspecter la structure et les contraintes de academic_years."""
from db import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

print("=== COLONNES ===")
cur.execute("SHOW COLUMNS FROM academic_years")
for c in cur.fetchall():
    print(f"  {c['Field']:25} {c['Type']:15} key={c['Key']} null={c['Null']}")

print("\n=== INDEX / CONTRAINTES ===")
cur.execute("SHOW INDEX FROM academic_years")
for i in cur.fetchall():
    print(f"  {i['Key_name']:25} col={i['Column_name']:20} unique={0 if i['Non_unique'] else 1} seq={i['Seq_in_index']}")

print("\n=== DONNEES (nom, school_id, est_active) ===")
cur.execute("SELECT id, nom, school_id, est_active FROM academic_years ORDER BY school_id, nom")
for r in cur.fetchall():
    print(f"  id={r['id']} nom={r['nom']} school_id={r['school_id']} active={r['est_active']}")

conn.close()
