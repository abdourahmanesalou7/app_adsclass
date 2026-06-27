"""
Script d'initialisation du système d'années académiques
Ce script crée les tables nécessaires et migre les données existantes
"""

import mysql.connector
from mysql.connector import Error
from datetime import datetime

# Configuration de la base de données
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'adsclass_bd'
}

def get_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"❌ Erreur de connexion: {e}")
        return None

def init_academic_years():
    conn = get_connection()
    if not conn:
        return False
    
    cursor = conn.cursor()
    
    try:
        # === CRÉATION DE LA TABLE DES ANNÉES ACADÉMIQUES ===
        print("📦 Création de la table academic_years...")
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS academic_years (
            id INT PRIMARY KEY AUTO_INCREMENT,
            nom VARCHAR(20) NOT NULL UNIQUE,
            date_debut DATE NOT NULL,
            date_fin DATE NOT NULL,
            est_active BOOLEAN DEFAULT FALSE,
            est_archivee BOOLEAN DEFAULT FALSE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_active (est_active),
            INDEX idx_archivee (est_archivee)
        )
        ''')
        print("✅ Table academic_years créée")
        
        # === AJOUT DE LA COLONNE annee_academique_id AUX TABLES EXISTANTES ===
        tables_to_update = [
            'courses',
            'paiements', 
            'presences',
            'notes',
            'absences',
            'documents',
            'depenses',
            'gradebook',
            'lectures',
            'exams',
            'assignments'
        ]
        
        for table in tables_to_update:
            try:
                # Vérifier si la colonne existe déjà
                cursor.execute(f"""
                    SELECT COUNT(*) FROM information_schema.COLUMNS 
                    WHERE TABLE_SCHEMA = 'adsclass_bd' 
                    AND TABLE_NAME = '{table}' 
                    AND COLUMN_NAME = 'annee_academique_id'
                """)
                exists = cursor.fetchone()[0] > 0
                
                if not exists:
                    print(f"📝 Ajout de annee_academique_id à {table}...")
                    cursor.execute(f"""
                        ALTER TABLE {table} 
                        ADD COLUMN annee_academique_id INT NULL,
                        ADD INDEX idx_{table}_annee (annee_academique_id)
                    """)
                    print(f"✅ Colonne ajoutée à {table}")
                else:
                    print(f"ℹ️ {table} a déjà la colonne annee_academique_id")
            except Error as e:
                if "doesn't exist" in str(e):
                    print(f"⚠️ Table {table} n'existe pas, ignorée")
                else:
                    print(f"⚠️ Erreur pour {table}: {e}")
        
        # === CRÉATION DE L'ANNÉE ACADÉMIQUE COURANTE ===
        current_year = datetime.now().year
        current_month = datetime.now().month
        
        # Déterminer l'année académique courante
        if current_month >= 9:  # Septembre ou après
            year_start = current_year
            year_end = current_year + 1
        else:
            year_start = current_year - 1
            year_end = current_year
        
        academic_year_name = f"{year_start}-{year_end}"
        
        # Vérifier si l'année existe
        cursor.execute("SELECT id FROM academic_years WHERE nom = %s", (academic_year_name,))
        result = cursor.fetchone()
        
        if not result:
            print(f"📅 Création de l'année académique {academic_year_name}...")
            cursor.execute('''
                INSERT INTO academic_years (nom, date_debut, date_fin, est_active, description)
                VALUES (%s, %s, %s, TRUE, %s)
            ''', (
                academic_year_name,
                f"{year_start}-09-01",
                f"{year_end}-08-31",
                f"Année académique {academic_year_name}"
            ))
            year_id = cursor.lastrowid
            print(f"✅ Année académique {academic_year_name} créée (ID: {year_id})")
            
            # Associer les données existantes à cette année
            print("🔄 Association des données existantes...")
            for table in tables_to_update:
                try:
                    cursor.execute(f"""
                        UPDATE {table} SET annee_academique_id = %s 
                        WHERE annee_academique_id IS NULL
                    """, (year_id,))
                    affected = cursor.rowcount
                    if affected > 0:
                        print(f"   ✅ {affected} enregistrements mis à jour dans {table}")
                except:
                    pass
        else:
            print(f"ℹ️ Année académique {academic_year_name} existe déjà")
        
        conn.commit()
        print("\n🎉 Initialisation du système d'années académiques terminée!")
        return True
        
    except Error as e:
        print(f"❌ Erreur: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    init_academic_years()

