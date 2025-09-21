#!/usr/bin/env python3
"""
Script de debug pour vérifier les cours et l'emploi du temps
"""

import mysql.connector
from mysql.connector import Error

def debug_courses():
    """Debug des cours et emploi du temps"""
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
        
        print("🔍 DEBUG COURS ET EMPLOI DU TEMPS")
        print("=" * 50)
        
        # 1. Vérifier tous les cours
        print("\n1. TOUS LES COURS:")
        cursor.execute("SELECT id, nom_cours, professeur_id, professeur_nom, filiere, jour_semaine, heure_debut, heure_fin FROM courses ORDER BY id")
        courses = cursor.fetchall()
        for course in courses:
            print(f"   ID: {course['id']} | {course['nom_cours']} | Prof: {course['professeur_nom']} | Filière: {course['filiere']} | Jour: {course['jour_semaine']}")
        
        # 2. Vérifier tous les professeurs
        print("\n2. TOUS LES PROFESSEURS:")
        cursor.execute("SELECT id, nom, prenom, email FROM users WHERE role = 'professeur' ORDER BY id")
        profs = cursor.fetchall()
        for prof in profs:
            print(f"   ID: {prof['id']} | {prof['prenom']} {prof['nom']} | {prof['email']}")
        
        # 3. Vérifier l'emploi du temps
        print("\n3. EMPLOI DU TEMPS:")
        cursor.execute("""
            SELECT et.*, c.nom_cours, u.prenom, u.nom 
            FROM emploi_temps et 
            JOIN courses c ON et.course_id = c.id 
            JOIN users u ON et.user_id = u.id 
            WHERE et.role = 'professeur' 
            ORDER BY et.user_id, et.course_id
        """)
        emploi = cursor.fetchall()
        for e in emploi:
            print(f"   Prof: {e['prenom']} {e['nom']} | Cours: {e['nom_cours']} | Visible: {e['visible']}")
        
        # 4. Vérifier les cours d'un professeur spécifique (Albert)
        print("\n4. COURS DU PROFESSEUR ALBERT (ID 3):")
        cursor.execute("""
            SELECT c.*, et.visible, et.notifications
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = 3 AND et.role = 'professeur' AND et.visible = 1
            ORDER BY c.jour_semaine, c.heure_debut
        """)
        albert_courses = cursor.fetchall()
        print(f"   Nombre de cours trouvés: {len(albert_courses)}")
        for course in albert_courses:
            print(f"   - {course['nom_cours']} | {course['jour_semaine']} {course['heure_debut']}-{course['heure_fin']}")
        
        conn.close()
        
    except Error as e:
        print(f"❌ Erreur: {e}")

if __name__ == "__main__":
    debug_courses()