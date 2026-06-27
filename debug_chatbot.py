import mysql.connector
import os

try:
    conn = mysql.connector.connect(
        host='localhost',
        database='adsclass_bd',
        user='root',
        password=''
    )
    cursor = conn.cursor(dictionary=True)

    # 1. Voir les documents récents avec chemins complets
    print('=== DOCUMENTS RECENTS (10 derniers) ===')
    cursor.execute('SELECT id, course_id, titre, chemin_fichier, visible FROM documents ORDER BY id DESC LIMIT 10')
    docs = cursor.fetchall()
    for d in docs:
        chemin = d['chemin_fichier']
        chemin_complet = os.path.join('uploads', chemin) if not chemin.startswith('uploads') else chemin
        existe = os.path.exists(chemin_complet)
        print(f"  ID:{d['id']} Course:{d['course_id']} Titre:{d['titre']}")
        print(f"    Chemin: {chemin} -> Existe: {'OUI' if existe else 'NON'}")
    
    if not docs:
        print("  AUCUN DOCUMENT TROUVE!")

    # 2. Voir les étudiants et leurs cours
    print('\n=== ETUDIANTS ET LEURS COURS ===')
    cursor.execute('''
        SELECT u.id, u.prenom, u.nom, et.course_id, c.nom_cours
        FROM users u
        JOIN emploi_temps et ON u.id = et.user_id
        JOIN courses c ON et.course_id = c.id
        WHERE u.role = 'etudiant' AND et.role = 'etudiant'
        LIMIT 10
    ''')
    etudiants = cursor.fetchall()
    for r in etudiants:
        print(f"  Etudiant {r['id']}: {r['prenom']} {r['nom']} -> Cours {r['course_id']}: {r['nom_cours']}")
    
    if not etudiants:
        print("  AUCUN ETUDIANT AVEC COURS!")

    # 3. Vérifier la jointure documents-cours-emploi_temps
    print('\n=== DOCUMENTS ACCESSIBLES PAR ETUDIANTS ===')
    cursor.execute('''
        SELECT d.id, d.titre, c.nom_cours, et.user_id
        FROM documents d
        JOIN courses c ON d.course_id = c.id
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.role = 'etudiant' AND d.visible = 1
        LIMIT 10
    ''')
    docs_access = cursor.fetchall()
    for r in docs_access:
        print(f"  Doc {r['id']}: {r['titre']} - Cours: {r['nom_cours']} - Etudiant: {r['user_id']}")
    
    # Vérifier les chemins des fichiers PDF
    print('\n=== VERIFICATION DES CHEMINS PDF ===')
    cursor.execute("SELECT id, chemin_fichier, titre FROM documents WHERE chemin_fichier LIKE '%.pdf' LIMIT 5")
    for d in cursor.fetchall():
        import os
        chemin = d['chemin_fichier']
        chemin_uploads = os.path.join('uploads', chemin)
        existe = os.path.exists(chemin_uploads)
        print(f"  Doc {d['id']}: {d['titre']}")
        print(f"    Chemin DB: {chemin}")
        print(f"    Chemin complet: {chemin_uploads}")
        print(f"    Existe: {'OUI' if existe else 'NON'}")

    if not docs_access:
        print("  AUCUN DOCUMENT ACCESSIBLE!")

        # Diagnostiquer le problème
        print("\n=== DIAGNOSTIC ===")
        
        # Compter les documents
        cursor.execute("SELECT COUNT(*) as count FROM documents")
        count = cursor.fetchone()['count']
        print(f"  Nombre total de documents: {count}")
        
        # Documents avec visible = 1
        cursor.execute("SELECT COUNT(*) as count FROM documents WHERE visible = 1")
        count_visible = cursor.fetchone()['count']
        print(f"  Documents visibles: {count_visible}")
        
        # Cours avec documents
        cursor.execute("SELECT DISTINCT course_id FROM documents")
        course_ids = cursor.fetchall()
        print(f"  Cours avec documents: {[c['course_id'] for c in course_ids]}")
        
        # Cours dans emploi_temps pour étudiants
        cursor.execute("SELECT DISTINCT course_id FROM emploi_temps WHERE role = 'etudiant'")
        et_courses = cursor.fetchall()
        print(f"  Cours dans emploi_temps (étudiants): {[c['course_id'] for c in et_courses]}")

    conn.close()
except Exception as e:
    import traceback
    print(f'Erreur: {e}')
    traceback.print_exc()

