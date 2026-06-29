"""Migration #8 : unicité multi-tenant des années académiques.

Remplace la contrainte UNIQUE globale sur academic_years.nom par une
contrainte composite UNIQUE (school_id, nom), afin que chaque école
puisse avoir sa propre "2026-2027".

Idempotent : peut être relancé sans risque. Vérifie d'abord qu'aucun
doublon (school_id, nom) n'existe avant de créer la contrainte.
"""
from db import get_db_connection


def _index_names(cursor, table):
    cursor.execute(f"SHOW INDEX FROM {table}")
    return {row[2] for row in cursor.fetchall()}  # Key_name


def run():
    conn = get_db_connection()
    if not conn:
        print("Erreur connexion DB")
        return False
    cursor = conn.cursor()
    try:
        # 1. Vérifier l'absence de doublons (school_id, nom)
        cursor.execute("""
            SELECT school_id, nom, COUNT(*) c
            FROM academic_years
            GROUP BY school_id, nom
            HAVING c > 1
        """)
        dups = cursor.fetchall()
        if dups:
            print("ABORT : doublons (school_id, nom) détectés, à nettoyer d'abord :")
            for d in dups:
                print(f"  school_id={d[0]} nom={d[1]} count={d[2]}")
            return False

        existing = _index_names(cursor, 'academic_years')

        # 2. Supprimer l'ancienne contrainte UNIQUE globale sur nom
        if 'nom' in existing:
            cursor.execute("ALTER TABLE academic_years DROP INDEX nom")
            print("OK: ancienne contrainte UNIQUE globale 'nom' supprimée.")
        else:
            print("Info: pas de contrainte UNIQUE globale 'nom' (déjà migrée).")

        # 3. Créer la contrainte composite UNIQUE (school_id, nom)
        existing = _index_names(cursor, 'academic_years')
        if 'uq_school_year' not in existing:
            cursor.execute(
                "ALTER TABLE academic_years "
                "ADD CONSTRAINT uq_school_year UNIQUE (school_id, nom)"
            )
            print("OK: contrainte composite UNIQUE (school_id, nom) créée.")
        else:
            print("Info: contrainte composite (school_id, nom) déjà présente.")

        conn.commit()
        print("\nMigration #8 terminée avec succès.")
        return True
    except Exception as e:
        conn.rollback()
        print(f"Erreur migration: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    run()
