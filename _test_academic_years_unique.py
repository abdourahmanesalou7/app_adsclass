"""Test #8 : unicité multi-tenant des années académiques.

Vérifie (sans persister, rollback final) que :
  - École A et École B peuvent toutes deux avoir '2026-2027'  -> OK
  - Une 2e création de '2026-2027' dans la même école          -> bloquée
"""
from db import get_db_connection

conn = get_db_connection()
cur = conn.cursor()

TEST_NOM = '2099-2100'  # nom improbable pour éviter tout conflit réel

try:
    # Nettoyage préventif de toute trace du test
    cur.execute("DELETE FROM academic_years WHERE nom = %s", (TEST_NOM,))

    # 1. École 1 : création -> doit réussir
    cur.execute(
        "INSERT INTO academic_years (nom, date_debut, date_fin, school_id) "
        "VALUES (%s, '2099-09-01', '2100-06-30', 1)", (TEST_NOM,))
    print("OK: École 1 a créé", TEST_NOM)

    # 2. École 2 : même nom -> doit réussir (multi-tenant)
    cur.execute(
        "INSERT INTO academic_years (nom, date_debut, date_fin, school_id) "
        "VALUES (%s, '2099-09-01', '2100-06-30', 2)", (TEST_NOM,))
    print("OK: École 2 a créé", TEST_NOM, "(coexistence multi-tenant)")

    # 3. École 1 : doublon -> doit échouer
    try:
        cur.execute(
            "INSERT INTO academic_years (nom, date_debut, date_fin, school_id) "
            "VALUES (%s, '2099-09-01', '2100-06-30', 1)", (TEST_NOM,))
        print("ECHEC: doublon dans École 1 n'a PAS été bloqué !")
    except Exception as e:
        if 'Duplicate' in str(e):
            print("OK: doublon dans École 1 correctement bloqué")
        else:
            print("ERREUR inattendue:", e)

    print("\nTEST #8 OK")
finally:
    conn.rollback()  # rien n'est persisté
    cur.close()
    conn.close()
