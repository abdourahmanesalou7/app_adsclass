#!/usr/bin/env python3
import mysql.connector

conn = mysql.connector.connect(
    host='localhost',
    user='root',
    password='',
    database='adsclass_bd'
)
cursor = conn.cursor(dictionary=True)

# Trouver Albert
cursor.execute("SELECT id, nom, prenom, email FROM users WHERE role = 'professeur' AND prenom LIKE '%Albert%'")
alberts = cursor.fetchall()
print("🔍 PROFESSEURS ALBERT:")
for albert in alberts:
    print(f"   ID: {albert['id']} | {albert['prenom']} {albert['nom']} | {albert['email']}")

# Vérifier les cours d'Albert (ID 6)
print(f"\n📚 COURS D'ALBERT (ID 6):")
cursor.execute("""
    SELECT c.*, et.visible, et.notifications
    FROM courses c
    JOIN emploi_temps et ON c.id = et.course_id
    WHERE et.user_id = 6 AND et.role = 'professeur' AND et.visible = 1
    ORDER BY c.jour_semaine, c.heure_debut
""")
albert_courses = cursor.fetchall()
print(f"   Nombre de cours trouvés: {len(albert_courses)}")
for course in albert_courses:
    print(f"   - {course['nom_cours']} | {course['jour_semaine']} {course['heure_debut']}-{course['heure_fin']}")

conn.close()