#!/usr/bin/env python3
"""
Corriger le cours récent sans professeur assigné
"""

import mysql.connector
from mysql.connector import Error

def fix_recent_course():
    """Corriger le cours Fintech en l'assignant à Albert Diompy"""
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
        
        print("🔧 CORRECTION DU COURS RÉCENT")
        print("=" * 40)
        
        # 1. Vérifier le cours Fintech
        cursor.execute("SELECT * FROM courses WHERE id = 13")
        course = cursor.fetchone()
        if course:
            print(f"📚 Cours trouvé: {course['nom_cours']}")
            print(f"   Professeur nom: '{course['professeur_nom']}'")
            print(f"   Professeur ID: {course['professeur_id']}")
            
            # 2. L'assigner à Albert Diompy
            cursor.execute("""
                UPDATE courses 
                SET professeur_id = 6, professeur_nom = 'Albert Diompy'
                WHERE id = 13
            """)
            print("✅ Cours assigné à Albert Diompy")
            
            # 3. L'ajouter à l'emploi du temps d'Albert
            cursor.execute("""
                INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications)
                VALUES (6, 13, 'professeur', 1, 1)
            """)
            print("✅ Cours ajouté à l'emploi du temps d'Albert")
            
            # 4. Ajouter les étudiants de la filière
            filiere = course['filiere']
            cursor.execute("SELECT id FROM users WHERE role = 'etudiant' AND filiere = %s", (filiere,))
            etudiants = cursor.fetchall()
            
            for etudiant in etudiants:
                cursor.execute("""
                    INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications)
                    VALUES (%s, 13, 'etudiant', 1, 1)
                """, (etudiant['id'],))
            
            print(f"✅ {len(etudiants)} étudiants de {filiere} ajoutés")
            
            conn.commit()
            print("🎉 Correction terminée avec succès!")
            
        else:
            print("❌ Cours ID 13 non trouvé")
        
        conn.close()
        
    except Error as e:
        print(f"❌ Erreur: {e}")

if __name__ == "__main__":
    fix_recent_course()