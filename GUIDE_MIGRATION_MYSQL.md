# Guide de Migration SQLite vers MySQL

## ✅ Migration Terminée

L'application Flask a été entièrement migrée de SQLite vers MySQL. Voici un résumé des modifications effectuées.

## 🔧 Modifications Principales

### 1. Imports et Connexion
- **Avant**: `import sqlite3`
- **Après**: `import mysql.connector` et `from mysql.connector import Error`

### 2. Fonction de Connexion
```python
# Nouvelle fonction get_db_connection() pour MySQL
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_db',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        return conn
    except Error as e:
        print(f"Erreur de connexion à MySQL: {e}")
        return None
```

### 3. Placeholders SQL
- **Avant**: `?` (SQLite)
- **Après**: `%s` (MySQL)

### 4. Cursor avec Dictionnaires
- **Avant**: `conn.row_factory = sqlite3.Row`
- **Après**: `cursor = conn.cursor(dictionary=True)`

### 5. Gestion des Erreurs
- **Avant**: `sqlite3.IntegrityError`
- **Après**: `mysql.connector.IntegrityError`

### 6. Syntaxe SQL Spécifique
- **INSERT OR REPLACE**: Remplacé par `INSERT INTO ... ON DUPLICATE KEY UPDATE`
- **INSERT OR IGNORE**: Remplacé par `INSERT IGNORE INTO`
- **date('now')**: Remplacé par `CURDATE()`
- **strftime**: Remplacé par `DATE_FORMAT`
- **YEAR(date)**: Fonction MySQL native

## 📋 Fonctions Migrées

### Routes Principales
- ✅ `register()` - Inscription utilisateur
- ✅ `login()` - Connexion utilisateur
- ✅ `prof_dashboard()` - Dashboard professeur
- ✅ `student_dashboard()` - Dashboard étudiant
- ✅ `admin_dashboard()` - Dashboard admin

### Gestion des Cours
- ✅ `add_course()` - Ajouter un cours
- ✅ `edit_course()` - Modifier un cours
- ✅ `delete_course()` - Supprimer un cours
- ✅ `professeur_emploi_temps()` - Emploi du temps professeur

### Gestion des Paiements
- ✅ `admin_finance()` - Gestion financière
- ✅ `etudiants_paiements()` - Paiements étudiants
- ✅ `ajouter_paiement()` - Ajouter un paiement
- ✅ `imprimer_recu()` - Impression reçu

### Gestion des Notes
- ✅ `admin_grades()` - Gestion des notes
- ✅ `student_grades()` - Notes étudiant
- ✅ `saisir_notes()` - Saisie des notes

### Gestion des Documents
- ✅ `upload_document()` - Upload de documents
- ✅ `download_document()` - Téléchargement
- ✅ `voir_documents_cours()` - Voir les documents

### Fonctions de Debug
- ✅ `debug_paiement()` - Debug paiements
- ✅ `debug_professeur()` - Debug professeur
- ✅ `test_prof_dashboard()` - Test dashboard

## 🗄️ Base de Données

### Configuration MySQL
- **Host**: localhost
- **User**: root
- **Password**: (vide)
- **Database**: adsclass_db
- **Charset**: utf8mb4
- **Collation**: utf8mb4_unicode_ci

### Tables Créées
- `users` - Utilisateurs (admin, professeur, étudiant)
- `courses` - Cours
- `paiements` - Paiements étudiants
- `depenses` - Dépenses
- `notes` - Notes des étudiants
- `absences` - Absences
- `emploi_temps` - Emploi du temps personnalisé
- `presences` - Présences aux cours
- `documents` - Documents uploadés

## 📦 Dépendances

### requirements.txt mis à jour
```
Flask
Werkzeug
mysql-connector-python
```

## 🧪 Test de Migration

Un script de test a été créé : `test_mysql_migration.py`

```bash
python test_mysql_migration.py
```

## 🚀 Démarrage

1. **Installer les dépendances**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Initialiser la base de données**:
   ```bash
   python init_bd.py
   ```

3. **Tester la migration**:
   ```bash
   python test_mysql_migration.py
   ```

4. **Lancer l'application**:
   ```bash
   python app.py
   ```

## ⚠️ Points d'Attention

1. **Connexion MySQL**: Assurez-vous que MySQL est démarré
2. **Base de données**: La base `adsclass_db` doit exister
3. **Permissions**: L'utilisateur `root` doit avoir les droits sur la base
4. **Charset**: Utilisation d'utf8mb4 pour supporter les caractères spéciaux

## 🔍 Vérifications

- ✅ Tous les imports SQLite remplacés
- ✅ Toutes les requêtes SQL adaptées
- ✅ Gestion d'erreurs MySQL implémentée
- ✅ Cursors avec dictionnaires configurés
- ✅ Fonctions de connexion mises à jour
- ✅ Requirements.txt mis à jour
- ✅ Script de test créé

## 📝 Notes

- La migration préserve toutes les fonctionnalités existantes
- Les templates HTML n'ont pas été modifiés
- La logique métier reste identique
- Seule la couche d'accès aux données a été modifiée

---

**Migration terminée avec succès !** 🎉