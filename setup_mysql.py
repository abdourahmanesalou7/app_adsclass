#!/usr/bin/env python3
"""
Script pour configurer MySQL et créer la base de données adsclass_db
"""

import mysql.connector
from mysql.connector import Error
import sys

def create_database():
    """Créer la base de données adsclass_db"""
    try:
        # Connexion sans spécifier de base de données
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        
        cursor = conn.cursor()
        
        # Créer la base de données
        cursor.execute("CREATE DATABASE IF NOT EXISTS adsclass_bd CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print("✅ Base de données 'adsclass_bd' créée avec succès")
        
        # Vérifier que la base existe
        cursor.execute("SHOW DATABASES")
        databases = cursor.fetchall()
        db_exists = any('adsclass_bd' in db for db in databases)
        
        if db_exists:
            print("✅ Base de données 'adsclass_bd' confirmée")
        else:
            print("❌ Erreur: Base de données non créée")
            return False
            
        conn.close()
        return True
        
    except Error as e:
        print(f"❌ Erreur lors de la création de la base de données: {e}")
        return False

def test_connection():
    """Tester la connexion à la base de données"""
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        print("✅ Connexion à la base de données 'adsclass_bd' réussie")
        conn.close()
        return True
        
    except Error as e:
        print(f"❌ Erreur de connexion: {e}")
        return False

def main():
    """Fonction principale"""
    print("🔧 Configuration de MySQL pour ADSClass")
    print("=" * 50)
    
    # Étape 1: Créer la base de données
    print("\n1. Création de la base de données...")
    if not create_database():
        print("❌ Échec de la création de la base de données")
        return 1
    
    # Étape 2: Tester la connexion
    print("\n2. Test de connexion...")
    if not test_connection():
        print("❌ Échec de la connexion")
        return 1
    
    print("\n" + "=" * 50)
    print("🎉 Configuration terminée avec succès!")
    print("\n📋 Prochaines étapes:")
    print("   1. Exécuter: python init_bd.py")
    print("   2. Exécuter: python test_mysql_migration.py")
    print("   3. Exécuter: python app.py")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())