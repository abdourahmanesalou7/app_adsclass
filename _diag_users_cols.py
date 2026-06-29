"""Diagnostic: colonnes de la table users (statut/soft-delete/actif)."""
from db import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)
cur.execute("SHOW COLUMNS FROM users")
for c in cur.fetchall():
    print(f"  {c['Field']:25} {c['Type']:18} null={c['Null']} default={c['Default']}")
conn.close()
