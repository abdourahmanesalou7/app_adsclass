import mysql.connector
from werkzeug.security import generate_password_hash
import smtplib
from email.message import EmailMessage

# --- Connexion à MySQL ---
conn = mysql.connector.connect(
    host='localhost',
    user='root',
    password='',
    database='adsclass_bd'
)
cursor = conn.cursor()

# --- Suppression des anciennes tables ---
tables = [
    'documents', 'presences', 'paiements', 'courses', 'users',
    'depenses', 'notes', 'absences', 'emploi_temps'
]
for table in tables:
    cursor.execute(f"DROP TABLE IF EXISTS {table}")

# --- Fonction pour générer automatiquement la classe ---
def generer_classe(niveau, filiere):
    if niveau is None or filiere is None:
        return None
    abbrev = niveau.split()[0][0] + niveau.split()[1]  # L1, L2, M1, etc.
    classe = f"{abbrev}-{filiere.replace(' ', '')}"
    return classe

# --- Création des tables ---
cursor.execute('''
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    nom VARCHAR(100) NOT NULL,
    prenom VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,
    filiere VARCHAR(100),
    niveau VARCHAR(50),
    classe VARCHAR(50),
    telephone VARCHAR(20),
    specialite VARCHAR(100)
)
''')

cursor.execute('''
CREATE TABLE courses (
    id INT PRIMARY KEY AUTO_INCREMENT,
    nom_cours VARCHAR(255) NOT NULL,
    professeur_id INT,
    professeur_nom VARCHAR(255),
    start DATETIME NOT NULL,
    end DATETIME NOT NULL,
    filiere VARCHAR(100) NOT NULL,
    salle VARCHAR(50),
    description TEXT,
    date_cours DATE,
    jour_semaine VARCHAR(20),
    heure_debut TIME,
    heure_fin TIME,
    recurrent TINYINT DEFAULT 1,
    FOREIGN KEY (professeur_id) REFERENCES users(id)
)
''')

cursor.execute('''
CREATE TABLE paiements (
    id INT PRIMARY KEY AUTO_INCREMENT,
    etudiant_id INT NOT NULL,
    date DATE NOT NULL,
    montant DOUBLE NOT NULL,
    moyen VARCHAR(50),
    observation TEXT,
    FOREIGN KEY (etudiant_id) REFERENCES users(id)
)
''')

cursor.execute('''
CREATE TABLE depenses (
    id INT PRIMARY KEY AUTO_INCREMENT,
    date DATE NOT NULL,
    description TEXT NOT NULL,
    montant DOUBLE NOT NULL CHECK (montant < 0)
)
''')

cursor.execute('''
CREATE TABLE notes (
    id INT PRIMARY KEY AUTO_INCREMENT,
    etudiant_id INT NOT NULL,
    nom_cours VARCHAR(255) NOT NULL,
    CC1 DOUBLE,
    CC2 DOUBLE,
    Participation DOUBLE,
    Examen DOUBLE,
    FOREIGN KEY (etudiant_id) REFERENCES users(id)
)
''')

cursor.execute('''
CREATE TABLE absences (
    id INT PRIMARY KEY AUTO_INCREMENT,
    etudiant_id INT NOT NULL,
    date DATE NOT NULL,
    heure TIME NOT NULL,
    motif TEXT,
    FOREIGN KEY (etudiant_id) REFERENCES users(id)
)
''')

cursor.execute('''
CREATE TABLE emploi_temps (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    course_id INT NOT NULL,
    role VARCHAR(50) NOT NULL,
    visible TINYINT DEFAULT 1,
    notifications TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (course_id) REFERENCES courses(id),
    UNIQUE(user_id, course_id)
)
''')

cursor.execute('''
CREATE TABLE presences (
    id INT PRIMARY KEY AUTO_INCREMENT,
    etudiant_id INT NOT NULL,
    course_id INT NOT NULL,
    professeur_id INT NOT NULL,
    date_cours DATE NOT NULL,
    statut VARCHAR(50) NOT NULL,
    commentaire TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (etudiant_id) REFERENCES users(id),
    FOREIGN KEY (course_id) REFERENCES courses(id),
    FOREIGN KEY (professeur_id) REFERENCES users(id),
    UNIQUE(etudiant_id, course_id, date_cours)
)
''')

cursor.execute('''
CREATE TABLE documents (
    id INT PRIMARY KEY AUTO_INCREMENT,
    course_id INT NOT NULL,
    professeur_id INT NOT NULL,
    titre VARCHAR(255) NOT NULL,
    description TEXT,
    nom_fichier VARCHAR(255) NOT NULL,
    chemin_fichier VARCHAR(255) NOT NULL,
    taille_fichier BIGINT,
    type_fichier VARCHAR(50),
    visible TINYINT DEFAULT 1,
    date_upload DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(id),
    FOREIGN KEY (professeur_id) REFERENCES users(id)
)
''')

# --- Fonctions utiles ---
def inserer_etudiant(nom, prenom, email, password, filiere, niveau, telephone=None):
    classe = generer_classe(niveau, filiere)
    password_hash = generate_password_hash(password)
    cursor.execute('''
    INSERT IGNORE INTO users (nom, prenom, email, password, role, filiere, niveau, classe, telephone)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (nom, prenom, email, password_hash, 'etudiant', filiere, niveau, classe, telephone))
    conn.commit()
    print(f"✅ Étudiant {prenom} {nom} ajouté avec la classe {classe}")

def envoyer_email(classes=None, sujet='', contenu='', email_expediteur='', motdepasse_app=''):
    if classes is None:
        cursor.execute('SELECT email FROM users WHERE role="etudiant"')
    else:
        placeholders = ','.join(['%s']*len(classes))
        cursor.execute(f'SELECT email FROM users WHERE classe IN ({placeholders})', classes)
    emails = [row[0] for row in cursor.fetchall()]

    if not emails:
        print("❌ Aucun étudiant trouvé pour les classes spécifiées")
        return

    msg = EmailMessage()
    msg['Subject'] = sujet
    msg['From'] = email_expediteur
    msg['To'] = ', '.join(emails)
    msg.set_content(contenu)

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(email_expediteur, motdepasse_app)
            server.send_message(msg)
            print(f"✅ Email envoyé à {len(emails)} étudiants")
    except Exception as e:
        print("❌ Erreur lors de l'envoi de l'email :", e)

# --- Insertion des utilisateurs ---
admin_password_hash = generate_password_hash('admin123')
cursor.execute('''
INSERT INTO users (nom, prenom, email, password, role, filiere, niveau, telephone, specialite)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
''', ('Admin', 'Super', 'admin@adsclass.ne', admin_password_hash, 'admin', None, None, '+212627616719', None))

# Étudiants
inserer_etudiant('Diallo', 'Aminata', 'aminata.diallo@adsclass.ne', 'student123', 'IA', 'Licence 1', '+22790123456')
inserer_etudiant('Moussa', 'Fatima', 'fatima.moussa@adsclass.ne', 'student123', 'IA', 'Licence 2', '+22792345678')
inserer_etudiant('Kone', 'Mariam', 'mariam.kone@adsclass.ne', 'student123', 'Data Science', 'Licence 1', '+22798765432')
inserer_etudiant('Traore', 'Ousmane', 'ousmane.traore@adsclass.ne', 'student123', 'Développement Web', 'Licence 2', '+22797654321')
inserer_etudiant('Sow', 'Aissatou', 'aissatou.sow@adsclass.ne', 'student123', 'Cybersécurité', 'Licence 1', '+22796543210')

# Professeurs
prof1_password_hash = generate_password_hash('prof123')
cursor.execute('''
INSERT INTO users (nom, prenom, email, password, role, filiere, niveau, telephone, specialite)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
''', ('Oumarou', 'Dr. Ibrahim', 'ibrahim.oumarou@adsclass.ne', prof1_password_hash, 'professeur', None, None, '+22796789012', 'Intelligence Artificielle'))

prof2_password_hash = generate_password_hash('prof123')
cursor.execute('''
INSERT INTO users (nom, prenom, email, password, role, filiere, niveau, telephone, specialite)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
''', ('Mamadou', 'Prof. Saidou', 'saidou.mamadou@adsclass.ne', prof2_password_hash, 'professeur', None, None, '+22794567890', 'Data Science'))

prof3_password_hash = generate_password_hash('prof123')
cursor.execute('''
INSERT INTO users (nom, prenom, email, password, role, filiere, niveau, telephone, specialite)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
''', ('Diompy', 'Albert', 'professeur.albert.diompy@adsclass.ne', prof3_password_hash, 'professeur', None, None, '+22790123456', 'Informatique Générale'))

# --- Insertion des cours ---
courses = [
    ("Introduction à l'IA", 1, 'Dr. Ibrahim Oumarou', '2026-09-02 08:00:00', '2026-09-02 10:00:00', 'IA', 'Salle A1', "Cours d'introduction aux concepts de base de l'IA", 'Lundi', '08:00:00', '10:00:00', 1),
    ('Machine Learning', 2, 'Prof. Saidou Mamadou', '2026-09-03 10:00:00', '2026-09-03 12:00:00', 'IA', 'Salle B2', 'Apprentissage automatique et algorithmes', 'Mardi', '10:00:00', '12:00:00', 1),
    ('Python Avancé', 1, 'Dr. Ibrahim Oumarou', '2026-09-04 14:00:00', '2026-09-04 16:00:00', 'IA', 'Lab Info', "Programmation Python pour l'IA", 'Mercredi', '14:00:00', '16:00:00', 1),
    ('Data Science', 2, 'Prof. Saidou Mamadou', '2026-09-05 08:00:00', '2026-09-05 10:00:00', 'IA', 'Salle C3', 'Analyse de données et visualisation', 'Jeudi', '08:00:00', '10:00:00', 1),
    ('Algorithmique Avancée', 3, 'Albert Diompy', '2024-12-20 09:00:00', '2024-12-20 11:00:00', 'IA', 'Salle D4', 'Structures de données et algorithmes complexes', 'Vendredi', '09:00:00', '11:00:00', 1),
    ('Bases de Données', 3, 'Albert Diompy', '2024-12-17 14:00:00', '2024-12-17 16:00:00', 'Data Science', 'Lab DB', 'Conception et gestion de bases de données', 'Mardi', '14:00:00', '16:00:00', 1),
    ('Programmation Web', 3, 'Albert Diompy', '2024-12-16 08:00:00', '2024-12-16 10:00:00', 'Développement Web', 'Lab Web', 'HTML, CSS, JavaScript et frameworks modernes', 'Lundi', '08:00:00', '10:00:00', 1),
    ('Sécurité Informatique', 3, 'Albert Diompy', '2024-12-18 10:00:00', '2024-12-18 12:00:00', 'Cybersécurité', 'Salle S1', 'Sécurité des systèmes et réseaux', 'Mercredi', '10:00:00', '12:00:00', 1)
]

for c in courses:
    cursor.execute('''
    INSERT INTO courses (nom_cours, professeur_id, professeur_nom, start, end, filiere, salle, description, jour_semaine, heure_debut, heure_fin, recurrent)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', c)

# Commit final
conn.commit()
conn.close()
print("✅ Base de données MySQL initialisée avec succès !")
