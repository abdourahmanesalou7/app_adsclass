"""
Script d'initialisation du système de rôles et permissions pour les administrateurs
Exécuter ce script pour créer les tables et les rôles par défaut
"""

import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash

# Connexion à la base de données
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        return conn
    except Error as e:
        print(f"Erreur de connexion à MySQL: {e}")
        return None

conn = get_db_connection()
if not conn:
    print("❌ Impossible de se connecter à la base de données")
    exit(1)

cursor = conn.cursor()

# Création de la table des rôles administratifs
print("📦 Création de la table admin_roles...")
cursor.execute('''
CREATE TABLE IF NOT EXISTS admin_roles (
    id INT PRIMARY KEY AUTO_INCREMENT,
    nom VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    couleur VARCHAR(20) DEFAULT '#6366f1',
    icone VARCHAR(50) DEFAULT 'fa-user-shield',
    priorite INT DEFAULT 0,
    actif BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)
''')
print("✅ Table admin_roles créée")

# Création de la table des permissions
print("📦 Création de la table admin_permissions...")
cursor.execute('''
CREATE TABLE IF NOT EXISTS admin_permissions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(100) NOT NULL UNIQUE,
    nom VARCHAR(100) NOT NULL,
    description TEXT,
    module VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
print("✅ Table admin_permissions créée")

# Création de la table de liaison rôles-permissions
print("📦 Création de la table admin_role_permissions...")
cursor.execute('''
CREATE TABLE IF NOT EXISTS admin_role_permissions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    role_id INT NOT NULL,
    permission_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES admin_roles(id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES admin_permissions(id) ON DELETE CASCADE,
    UNIQUE KEY unique_role_permission (role_id, permission_id)
)
''')
print("✅ Table admin_role_permissions créée")

# Ajouter la colonne admin_role_id à la table users si elle n'existe pas
print("📦 Ajout de la colonne admin_role_id à la table users...")
try:
    cursor.execute('''
        ALTER TABLE users ADD COLUMN admin_role_id INT DEFAULT NULL,
        ADD FOREIGN KEY (admin_role_id) REFERENCES admin_roles(id) ON DELETE SET NULL
    ''')
    print("✅ Colonne admin_role_id ajoutée")
except mysql.connector.Error as e:
    if 'Duplicate column name' in str(e) or '1060' in str(e):
        print("ℹ️ Colonne admin_role_id existe déjà")
    else:
        print(f"⚠️ Erreur: {e}")

conn.commit()

# === INSERTION DES PERMISSIONS PAR MODULE ===
print("\n📝 Insertion des permissions...")

permissions = [
    # Module COURS
    ('cours.view', 'Voir les cours', 'Accès en lecture aux cours', 'cours'),
    ('cours.create', 'Créer des cours', 'Créer de nouveaux cours', 'cours'),
    ('cours.edit', 'Modifier les cours', 'Modifier les cours existants', 'cours'),
    ('cours.delete', 'Supprimer les cours', 'Supprimer des cours', 'cours'),
    ('cours.assign', 'Assigner professeurs', 'Assigner des professeurs aux cours', 'cours'),
    
    # Module FINANCES
    ('finance.view', 'Voir les finances', 'Accès aux données financières', 'finance'),
    ('finance.create', 'Créer des paiements', 'Enregistrer de nouveaux paiements', 'finance'),
    ('finance.edit', 'Modifier les paiements', 'Modifier les paiements', 'finance'),
    ('finance.delete', 'Supprimer les paiements', 'Supprimer des paiements', 'finance'),
    ('finance.reports', 'Rapports financiers', 'Générer des rapports financiers', 'finance'),
    ('finance.export', 'Exporter les finances', 'Exporter les données financières', 'finance'),
    
    # Module NOTES
    ('notes.view', 'Voir les notes', 'Accès aux notes des étudiants', 'notes'),
    ('notes.create', 'Créer des notes', 'Saisir de nouvelles notes', 'notes'),
    ('notes.edit', 'Modifier les notes', 'Modifier les notes existantes', 'notes'),
    ('notes.delete', 'Supprimer les notes', 'Supprimer des notes', 'notes'),
    ('notes.bulletin', 'Générer bulletins', 'Générer les bulletins de notes', 'notes'),
    
    # Module PRESENCES
    ('presences.view', 'Voir les présences', 'Accès aux présences', 'presences'),
    ('presences.mark', 'Marquer présences', 'Marquer les présences/absences', 'presences'),
    ('presences.reports', 'Rapports présences', 'Générer des rapports de présences', 'presences'),
    
    # Module UTILISATEURS
    ('users.view', 'Voir les utilisateurs', 'Accès à la liste des utilisateurs', 'users'),
    ('users.create', 'Créer des utilisateurs', 'Créer de nouveaux comptes', 'users'),
    ('users.edit', 'Modifier les utilisateurs', 'Modifier les comptes utilisateurs', 'users'),
    ('users.delete', 'Supprimer les utilisateurs', 'Supprimer des comptes', 'users'),
    ('users.roles', 'Gérer les rôles', 'Attribuer des rôles aux utilisateurs', 'users'),
    
    # Module CLASSES
    ('classes.view', 'Voir les classes', 'Accès aux classes', 'classes'),
    ('classes.manage', 'Gérer les classes', 'Gérer les classes et filières', 'classes'),
    
    # Module STATISTIQUES
    ('stats.view', 'Voir les statistiques', 'Accès aux statistiques', 'stats'),
    ('stats.export', 'Exporter les stats', 'Exporter les statistiques', 'stats'),
    
    # Module SYSTEME
    ('system.settings', 'Paramètres système', 'Accès aux paramètres système', 'system'),
    ('system.logs', 'Voir les logs', 'Accès aux logs système', 'system'),
    ('system.backup', 'Sauvegardes', 'Gérer les sauvegardes', 'system'),

    # Module ADMISSIONS CRM
    ('admissions.view', 'Voir admissions', 'Accès au pipeline admissions', 'admissions'),
    ('admissions.create', 'Créer candidats', 'Créer des prospects et candidats', 'admissions'),
    ('admissions.edit', 'Modifier candidats', 'Modifier les dossiers candidats', 'admissions'),
    ('admissions.convert', 'Convertir en étudiant', 'Convertir un candidat admis en étudiant', 'admissions'),
    ('admissions.communicate', 'Communications', 'Envoyer WhatsApp, email, SMS', 'admissions'),
    ('admissions.reports', 'Statistiques admissions', 'Rapports et analytics admissions', 'admissions'),
    ('admissions.payments', 'Paiements en ligne', 'Gérer les paiements en ligne admissions', 'admissions'),
    ('admissions.filieres', 'Filières admissions', 'Configurer filières et frais inscription', 'admissions'),
]

for code, nom, desc, module in permissions:
    try:
        cursor.execute('''
            INSERT IGNORE INTO admin_permissions (code, nom, description, module)
            VALUES (%s, %s, %s, %s)
        ''', (code, nom, desc, module))
    except:
        pass

conn.commit()
print(f"✅ {len(permissions)} permissions créées")

# === CRÉATION DES RÔLES PAR DÉFAUT ===
print("\n👥 Création des rôles par défaut...")

roles = [
    ('Super Administrateur', 'Accès complet à toutes les fonctionnalités', '#dc2626', 'fa-crown', 100),
    ('Directeur', 'Gestion globale de l\'établissement', '#7c3aed', 'fa-building', 90),
    ('Responsable Pédagogique', 'Gestion des cours, notes et présences', '#2563eb', 'fa-graduation-cap', 80),
    ('Responsable Admissions', 'Gestion complète du pipeline candidats et inscriptions', '#0ea5e9', 'fa-inbox', 75),
    ('Comptable', 'Gestion financière complète', '#059669', 'fa-calculator', 70),
    ('Secrétaire', 'Gestion administrative de base', '#d97706', 'fa-clipboard', 60),
    ('Surveillant', 'Gestion des présences uniquement', '#6366f1', 'fa-eye', 50),
]

for nom, desc, couleur, icone, priorite in roles:
    try:
        cursor.execute('''
            INSERT IGNORE INTO admin_roles (nom, description, couleur, icone, priorite)
            VALUES (%s, %s, %s, %s, %s)
        ''', (nom, desc, couleur, icone, priorite))
    except:
        pass

conn.commit()
print(f"✅ {len(roles)} rôles créés")

# === ATTRIBUTION DES PERMISSIONS AUX RÔLES ===
print("\n🔗 Attribution des permissions aux rôles...")

# Récupérer les IDs des rôles
cursor.execute("SELECT id, nom FROM admin_roles")
roles_db = {nom: id for id, nom in cursor.fetchall()}

# Récupérer les IDs des permissions
cursor.execute("SELECT id, code FROM admin_permissions")
perms_db = {code: id for id, code in cursor.fetchall()}

# Définir les permissions par rôle
role_permissions = {
    'Super Administrateur': list(perms_db.keys()),  # Toutes les permissions
    'Directeur': [
        'cours.view', 'cours.create', 'cours.edit', 'cours.delete', 'cours.assign',
        'finance.view', 'finance.reports', 'finance.export',
        'notes.view', 'notes.bulletin',
        'presences.view', 'presences.reports',
        'users.view', 'users.create', 'users.edit', 'users.roles',
        'classes.view', 'classes.manage',
        'stats.view', 'stats.export',
        'system.settings', 'system.logs',
        'admissions.view', 'admissions.create', 'admissions.edit', 'admissions.convert', 'admissions.communicate', 'admissions.reports',
    ],
    'Responsable Pédagogique': [
        'cours.view', 'cours.create', 'cours.edit', 'cours.assign',
        'notes.view', 'notes.create', 'notes.edit', 'notes.bulletin',
        'presences.view', 'presences.mark', 'presences.reports',
        'classes.view',
        'stats.view',
    ],
    'Responsable Admissions': [
        'admissions.view', 'admissions.create', 'admissions.edit', 'admissions.convert',
        'admissions.communicate', 'admissions.reports', 'admissions.payments', 'admissions.filieres',
        'users.view', 'stats.view', 'classes.view',
    ],
    'Comptable': [
        'finance.view', 'finance.create', 'finance.edit', 'finance.reports', 'finance.export',
        'users.view',
        'stats.view',
    ],
    'Secrétaire': [
        'cours.view',
        'users.view', 'users.create', 'users.edit',
        'classes.view',
        'presences.view',
        'stats.view',
        'admissions.view', 'admissions.create', 'admissions.edit', 'admissions.communicate',
    ],
    'Surveillant': [
        'presences.view', 'presences.mark',
        'classes.view',
    ],
}

for role_nom, perm_codes in role_permissions.items():
    if role_nom in roles_db:
        role_id = roles_db[role_nom]
        for perm_code in perm_codes:
            if perm_code in perms_db:
                perm_id = perms_db[perm_code]
                try:
                    cursor.execute('''
                        INSERT IGNORE INTO admin_role_permissions (role_id, permission_id)
                        VALUES (%s, %s)
                    ''', (role_id, perm_id))
                except:
                    pass

conn.commit()
print("✅ Permissions attribuées aux rôles")

# === MISE À JOUR DE L'ADMIN EXISTANT ===
print("\n👤 Mise à jour de l'administrateur existant...")

# Attribuer le rôle Super Administrateur à l'admin existant
if 'Super Administrateur' in roles_db:
    cursor.execute('''
        UPDATE users SET admin_role_id = %s
        WHERE role = 'admin' AND admin_role_id IS NULL
    ''', (roles_db['Super Administrateur'],))
    updated = cursor.rowcount
    print(f"✅ {updated} administrateur(s) mis à jour avec le rôle Super Administrateur")

conn.commit()
cursor.close()
conn.close()

print("\n" + "="*50)
print("🎉 Système de rôles et permissions initialisé avec succès!")
print("="*50)
print("\nRôles disponibles:")
for nom, desc, couleur, icone, priorite in roles:
    print(f"  • {nom} (priorité: {priorite})")
print("\nVous pouvez maintenant attribuer des rôles aux administrateurs.")
print("="*50)

