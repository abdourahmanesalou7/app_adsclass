#!/usr/bin/env python3
"""
Test simple de connexion à la base de données
"""

import mysql.connector
from mysql.connector import Error

def test_connection():
    """Test de connexion à la base de données"""
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        print("✅ Connexion à la base de données réussie")
        
        cursor = conn.cursor(dictionary=True)
        
        # Test simple
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'professeur'")
        result = cursor.fetchone()
        print(f"📊 Nombre de professeurs: {result['count']}")
        
        cursor.execute("SELECT COUNT(*) as count FROM courses")
        result = cursor.fetchone()
        print(f"📚 Nombre de cours: {result['count']}")
        
        conn.close()
        print("✅ Connexion fermée")
        
    except Error as e:
        print(f"❌ Erreur de connexion: {e}")

if __name__ == "__main__":
    test_connection()