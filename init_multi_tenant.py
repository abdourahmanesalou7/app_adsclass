"""
Multi-tenant ADSClass — Phase 3
Idempotent : sûr à ré-exécuter.

1. Crée la table `schools`
2. Insère l'École par défaut (id=1) — tout l'existant lui sera rattaché
3. Ajoute la colonne `school_id INT NOT NULL DEFAULT 1` sur les tables d'entités
4. Crée index + foreign keys vers schools(id)

Aucune donnée n'est supprimée ni déplacée.
"""
from db import get_db_connection, DB_CONFIG

# Tables d'entités qui doivent porter school_id (scope multi-tenant)
TENANT_TABLES = [
    'users', 'courses', 'paiements', 'depenses', 'notes', 'absences',
    'emploi_temps', 'presences', 'documents', 'academic_years',
    'filieres', 'modules', 'assignments', 'exams', 'gradebook',
    'lectures', 'presences_generales', 'notifications',
    'admissions_candidats', 'demandes_attestations', 'import_jobs',
]


def column_exists(cur, table, column):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (DB_CONFIG['database'], table, column))
    return cur.fetchone()[0] > 0


def table_exists(cur, table):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
    """, (DB_CONFIG['database'], table))
    return cur.fetchone()[0] > 0


def fk_exists(cur, table, fk_name):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s
              AND CONSTRAINT_TYPE='FOREIGN KEY'
    """, (DB_CONFIG['database'], table, fk_name))
    return cur.fetchone()[0] > 0


def index_exists(cur, table, idx_name):
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s
    """, (DB_CONFIG['database'], table, idx_name))
    return cur.fetchone()[0] > 0


def main():
    conn = get_db_connection()
    if not conn:
        print("❌ Connexion DB impossible")
        return 1
    cur = conn.cursor()
    try:
        # === 1. Table schools ===
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schools (
                id INT PRIMARY KEY AUTO_INCREMENT,
                nom VARCHAR(200) NOT NULL,
                code VARCHAR(50) UNIQUE,
                domaine VARCHAR(150) UNIQUE NULL,
                database_name VARCHAR(100) NULL,
                pays VARCHAR(80) DEFAULT 'Niger',
                ville VARCHAR(100) NULL,
                adresse TEXT NULL,
                telephone VARCHAR(30) NULL,
                email_contact VARCHAR(150) NULL,
                logo VARCHAR(255) NULL,
                couleur_primaire VARCHAR(20) DEFAULT '#6366f1',
                devise VARCHAR(10) DEFAULT 'XOF',
                timezone VARCHAR(50) DEFAULT 'Africa/Niamey',
                statut ENUM('active','suspended','trial','archived') DEFAULT 'active',
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_statut (statut),
                INDEX idx_domaine (domaine)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print("✅ Table schools prête")

        # === 2. École par défaut (id=1) ===
        cur.execute("SELECT id FROM schools WHERE id=1")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO schools (id, nom, code, pays, devise, statut, email_contact)
                VALUES (1, 'École par défaut', 'DEFAULT', 'Niger', 'XOF', 'active', 'admin@adsclass.ne')
            """)
            print("✅ École par défaut (id=1) créée")
        else:
            print("ℹ École par défaut déjà présente")

        # === 3. Ajout school_id sur chaque table d'entité ===
        added, skipped = 0, 0
        for tbl in TENANT_TABLES:
            if not table_exists(cur, tbl):
                print(f"⚠ {tbl} : absente — skip")
                continue
            if column_exists(cur, tbl, 'school_id'):
                skipped += 1
                continue
            cur.execute(f"ALTER TABLE {tbl} ADD COLUMN school_id INT NOT NULL DEFAULT 1")
            print(f"  + school_id ajouté à {tbl}")
            added += 1

        conn.commit()

        # === 4. Index + FK ===
        for tbl in TENANT_TABLES:
            if not table_exists(cur, tbl) or not column_exists(cur, tbl, 'school_id'):
                continue
            idx = f"idx_{tbl}_school"
            if not index_exists(cur, tbl, idx):
                cur.execute(f"CREATE INDEX {idx} ON {tbl}(school_id)")
            fk = f"fk_{tbl}_school"
            if not fk_exists(cur, tbl, fk):
                try:
                    cur.execute(f"""
                        ALTER TABLE {tbl}
                        ADD CONSTRAINT {fk} FOREIGN KEY (school_id)
                        REFERENCES schools(id) ON DELETE RESTRICT ON UPDATE CASCADE
                    """)
                except Exception as e:
                    print(f"  ⚠ FK {fk} non créée : {e}")

        conn.commit()
        print(f"✅ Migration : {added} colonne(s) ajoutée(s), {skipped} déjà présente(s), FK/index OK")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
