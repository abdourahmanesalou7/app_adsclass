#!/usr/bin/env python3
"""
Script de test pour vérifier la migration de SQLite vers MySQL
"""

import mysql.connector
from mysql.connector import Error
import sys

def test_connection():
    """Test de connexion à MySQL"""
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        print("✅ Connexion MySQL réussie")
        
        # Test des tables principales
        cursor = conn.cursor(dictionary=True)
        
        # Test table users
        cursor.execute("SELECT COUNT(*) as count FROM users")
        users_count = cursor.fetchone()['count']
        print(f"✅ Table users: {users_count} utilisateurs")
        
        # Test table courses
        cursor.execute("SELECT COUNT(*) as count FROM courses")
        courses_count = cursor.fetchone()['count']
        print(f"✅ Table courses: {courses_count} cours")
        
        # Test table paiements
        cursor.execute("SELECT COUNT(*) as count FROM paiements")
        paiements_count = cursor.fetchone()['count']
        print(f"✅ Table paiements: {paiements_count} paiements")
        
        # Test table emploi_temps
        cursor.execute("SELECT COUNT(*) as count FROM emploi_temps")
        emploi_count = cursor.fetchone()['count']
        print(f"✅ Table emploi_temps: {emploi_count} entrées")
        
        # Test des rôles
        cursor.execute("SELECT role, COUNT(*) as count FROM users GROUP BY role")
        roles = cursor.fetchall()
        print("✅ Répartition des rôles:")
        for role in roles:
            print(f"   - {role['role']}: {role['count']} utilisateurs")
        
        conn.close()
        return True
        
    except Error as e:
        print(f"❌ Erreur de connexion MySQL: {e}")
        return False

def test_app_import():
    """Test d'import de l'application Flask"""
    try:
        from app import app, get_db_connection
        print("✅ Import de l'application Flask réussi")
        
        # Test de la fonction get_db_connection
        conn = get_db_connection()
        if conn:
            print("✅ Fonction get_db_connection() fonctionne")
            conn.close()
            return True
        else:
            print("❌ Fonction get_db_connection() a échoué")
            return False
            
    except Exception as e:
        print(f"❌ Erreur d'import de l'application: {e}")
        return False

def main():
    """Fonction principale de test"""
    print("🧪 Test de migration SQLite vers MySQL")
    print("=" * 50)
    
    # Test 1: Connexion MySQL
    print("\n1. Test de connexion MySQL...")
    mysql_ok = test_connection()
    
    # Test 2: Import de l'application
    print("\n2. Test d'import de l'application...")
    app_ok = test_app_import()
    
    # Résumé
    print("\n" + "=" * 50)
    print("📊 RÉSUMÉ DES TESTS:")
    print(f"   MySQL: {'✅ OK' if mysql_ok else '❌ ÉCHEC'}")
    print(f"   App: {'✅ OK' if app_ok else '❌ ÉCHEC'}")
    
    if mysql_ok and app_ok:
        print("\n🎉 Migration réussie ! L'application est prête à fonctionner avec MySQL.")
        return 0
    else:
        print("\n⚠️  Des problèmes ont été détectés. Vérifiez la configuration.")
        return 1

if __name__ == "__main__":
    sys.exit(main())