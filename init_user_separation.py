"""
Sépare proprement les utilisateurs par rôle SANS toucher à la table `users`
(qui reste la table d'authentification commune).

Crée 3 tables de profil (1-1 avec users) :
  - students_profiles
  - professors_profiles
  - administrators_profiles

Et 3 vues SQL prêtes à consommer :
  - vw_students, vw_professeurs, vw_administrators

Idempotent : peut être exécuté plusieurs fois sans risque.
Usage : python init_user_separation.py
"""
from db import get_db_connection

DDL = [
    # --- Profils étudiants ---
    """
    CREATE TABLE IF NOT EXISTS students_profiles (
        user_id INT PRIMARY KEY,
        matricule VARCHAR(50) UNIQUE,
        date_naissance DATE NULL,
        lieu_naissance VARCHAR(150) NULL,
        sexe ENUM('M','F','Autre') NULL,
        nationalite VARCHAR(80) NULL,
        adresse TEXT NULL,
        ville VARCHAR(100) NULL,
        parent_nom VARCHAR(150) NULL,
        parent_telephone VARCHAR(30) NULL,
        parent_email VARCHAR(150) NULL,
        photo VARCHAR(255) NULL,
        date_inscription DATE NULL,
        statut_inscription ENUM('actif','suspendu','diplome','abandonne') DEFAULT 'actif',
        annee_academique_id INT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_matricule (matricule),
        INDEX idx_statut (statut_inscription)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # --- Profils professeurs ---
    """
    CREATE TABLE IF NOT EXISTS professors_profiles (
        user_id INT PRIMARY KEY,
        matricule VARCHAR(50) UNIQUE,
        diplome VARCHAR(150) NULL,
        grade VARCHAR(100) NULL,
        departement VARCHAR(150) NULL,
        date_embauche DATE NULL,
        type_contrat ENUM('CDI','CDD','Vacataire','Stagiaire') DEFAULT 'CDI',
        salaire_base DECIMAL(12,2) NULL,
        biographie TEXT NULL,
        photo VARCHAR(255) NULL,
        cv_fichier VARCHAR(255) NULL,
        statut ENUM('actif','suspendu','retraite','demission') DEFAULT 'actif',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_matricule (matricule),
        INDEX idx_departement (departement)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # --- Profils administrateurs ---
    """
    CREATE TABLE IF NOT EXISTS administrators_profiles (
        user_id INT PRIMARY KEY,
        matricule VARCHAR(50) UNIQUE,
        service VARCHAR(150) NULL,
        fonction VARCHAR(150) NULL,
        date_embauche DATE NULL,
        type_contrat ENUM('CDI','CDD','Vacataire','Stagiaire') DEFAULT 'CDI',
        photo VARCHAR(255) NULL,
        statut ENUM('actif','suspendu','demission') DEFAULT 'actif',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_service (service)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

VIEWS = [
    ("vw_students", """
        CREATE OR REPLACE VIEW vw_students AS
        SELECT u.id, u.nom, u.prenom, u.email, u.telephone,
               u.filiere, u.niveau, u.classe,
               sp.matricule, sp.date_naissance, sp.lieu_naissance, sp.sexe,
               sp.nationalite, sp.adresse, sp.ville,
               sp.parent_nom, sp.parent_telephone, sp.parent_email,
               sp.photo, sp.date_inscription, sp.statut_inscription,
               sp.annee_academique_id, sp.created_at, sp.updated_at
        FROM users u
        LEFT JOIN students_profiles sp ON sp.user_id = u.id
        WHERE u.role = 'etudiant'
    """),
    ("vw_professeurs", """
        CREATE OR REPLACE VIEW vw_professeurs AS
        SELECT u.id, u.nom, u.prenom, u.email, u.telephone, u.specialite,
               pp.matricule, pp.diplome, pp.grade, pp.departement,
               pp.date_embauche, pp.type_contrat, pp.salaire_base,
               pp.biographie, pp.photo, pp.cv_fichier, pp.statut,
               pp.created_at, pp.updated_at
        FROM users u
        LEFT JOIN professors_profiles pp ON pp.user_id = u.id
        WHERE u.role = 'professeur'
    """),
    ("vw_administrators", """
        CREATE OR REPLACE VIEW vw_administrators AS
        SELECT u.id, u.nom, u.prenom, u.email, u.telephone,
               ap.matricule, ap.service, ap.fonction, ap.date_embauche,
               ap.type_contrat, ap.photo, ap.statut,
               ap.created_at, ap.updated_at
        FROM users u
        LEFT JOIN administrators_profiles ap ON ap.user_id = u.id
        WHERE u.role = 'admin'
    """),
]


def run():
    conn = get_db_connection()
    if not conn:
        print("❌ Connexion DB impossible")
        return 1
    cursor = conn.cursor()
    try:
        for ddl in DDL:
            cursor.execute(ddl)
        for name, view_sql in VIEWS:
            cursor.execute(view_sql)
            print(f"✅ Vue {name} créée/mise à jour")
        conn.commit()
        print("✅ Séparation users : tables profils + vues prêtes")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        return 2
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
