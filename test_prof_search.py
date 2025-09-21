#!/usr/bin/env python3
"""
Test de la recherche de professeur par nom
"""

import mysql.connector
from mysql.connector import Error

def test_prof_search():
    """Test de la recherche de professeur par nom"""
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        cursor = conn.cursor(dictionary=True)
        
        print("🔍 TEST DE RECHERCHE DE PROFESSEUR PAR NOM")
        print("=" * 50)
        
        # 1. Lister tous les professeurs
        print("\n1. Tous les professeurs dans la base :")
        cursor.execute("SELECT id, nom, prenom, CONCAT(prenom, ' ', nom) as nom_complet FROM users WHERE role = 'professeur'")
        profs = cursor.fetchall()
        for prof in profs:
            print(f"   ID: {prof['id']} - {prof['nom_complet']}")
        
        # 2. Test de recherche avec différents noms
        test_names = [
            "Albert Diompy",
            "Albert",
            "Diompy", 
            "albert diompy",
            "ALBERT DIOMPY",
            "Albert DIOMPY"
        ]
        
        print(f"\n2. Tests de recherche :")
        for test_name in test_names:
            print(f"\n   Recherche: '{test_name}'")
            cursor.execute("""
                SELECT id, nom, prenom, CONCAT(prenom, ' ', nom) as nom_complet FROM users
                WHERE role = 'professeur'
                AND (LOWER(CONCAT(nom, ' ', prenom)) LIKE LOWER(%s)
                     OR LOWER(CONCAT(prenom, ' ', nom)) LIKE LOWER(%s))
                LIMIT 1
            """, (f'%{test_name}%', f'%{test_name}%'))
            
            result = cursor.fetchone()
            if result:
                print(f"   ✅ Trouvé: ID {result['id']} - {result['nom_complet']}")
            else:
                print(f"   ❌ Non trouvé")
        
        # 3. Test avec un cours récent
        print(f"\n3. Cours récents créés :")
        cursor.execute("""
            SELECT id, nom_cours, professeur_nom, professeur_id, start, end 
            FROM courses 
            ORDER BY id DESC 
            LIMIT 5
        """)
        recent_courses = cursor.fetchall()
        for course in recent_courses:
            print(f"   ID: {course['id']} - {course['nom_cours']}")
            print(f"      Professeur nom: '{course['professeur_nom']}'")
            print(f"      Professeur ID: {course['professeur_id']}")
            print(f"      Date: {course['start']}")
            print()
        
        conn.close()
        
    except Error as e:
        print(f"❌ Erreur: {e}")

if __name__ == "__main__":
    test_prof_search()