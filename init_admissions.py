"""
Initialisation Admissions CRM v2 — permissions + rôle Responsable Admissions
Exécuter: python init_admissions.py
"""
import mysql.connector

DB_CONFIG = {
    'host': 'localhost', 'user': 'root', 'password': '',
    'database': 'adsclass_bd', 'charset': 'utf8mb4',
}

permissions = [
    ('admissions.view', 'Voir admissions', 'Accès au pipeline admissions', 'admissions'),
    ('admissions.create', 'Créer candidats', 'Créer des prospects et candidats', 'admissions'),
    ('admissions.edit', 'Modifier candidats', 'Modifier les dossiers candidats', 'admissions'),
    ('admissions.convert', 'Convertir en étudiant', 'Convertir un candidat admis en étudiant', 'admissions'),
    ('admissions.communicate', 'Communications', 'Envoyer WhatsApp, email, SMS', 'admissions'),
    ('admissions.reports', 'Statistiques admissions', 'Rapports et analytics admissions', 'admissions'),
    ('admissions.payments', 'Paiements en ligne', 'Gérer les paiements en ligne admissions', 'admissions'),
    ('admissions.filieres', 'Filières admissions', 'Configurer filières et frais inscription', 'admissions'),
]

conn = mysql.connector.connect(**DB_CONFIG)
cursor = conn.cursor()

for code, nom, desc, module in permissions:
    cursor.execute('INSERT IGNORE INTO admin_permissions (code, nom, description, module) VALUES (%s,%s,%s,%s)',
                   (code, nom, desc, module))

# Créer rôle Responsable Admissions
cursor.execute('''
    INSERT IGNORE INTO admin_roles (nom, description, couleur, icone, priorite)
    VALUES ('Responsable Admissions', 'Gestion complète du pipeline candidats et inscriptions', '#0ea5e9', 'fa-inbox', 75)
''')

conn.commit()

cursor.execute("SELECT id, nom FROM admin_roles")
roles = {nom: rid for rid, nom in cursor.fetchall()}
cursor.execute("SELECT id, code FROM admin_permissions WHERE module='admissions'")
perms = {code: pid for pid, code in cursor.fetchall()}

admissions_perms = list(perms.keys())
role_map = {
    'Super Administrateur': admissions_perms,
    'Directeur': admissions_perms,
    'Responsable Admissions': admissions_perms,
    'Secrétaire': ['admissions.view', 'admissions.create', 'admissions.edit', 'admissions.communicate', 'admissions.filieres'],
    'Comptable': ['admissions.view', 'admissions.payments', 'admissions.reports'],
}

for role_nom, codes in role_map.items():
    if role_nom not in roles:
        continue
    for code in codes:
        if code in perms:
            cursor.execute('INSERT IGNORE INTO admin_role_permissions (role_id, permission_id) VALUES (%s,%s)',
                           (roles[role_nom], perms[code]))

conn.commit()
cursor.close()
conn.close()
print("✅ Admissions CRM v2 — permissions & rôle Responsable Admissions initialisés")
