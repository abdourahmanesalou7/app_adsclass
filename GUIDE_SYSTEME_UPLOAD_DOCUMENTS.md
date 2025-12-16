# 📚 Guide du Système d'Upload de Documents

## 🎯 **Vue d'ensemble**

Le système d'upload de documents permet aux professeurs de partager des ressources pédagogiques avec leurs étudiants de manière sécurisée et organisée.

## ✨ **Fonctionnalités Principales**

### **1. Pour les Professeurs**
- ✅ **Bouton "Upload Document"** dans l'emploi du temps
- ✅ **Interface d'upload moderne** avec drag & drop
- ✅ **Types de documents** : Cours, Exercice, Correction, Ressource
- ✅ **Validation des fichiers** : PDF, DOC, DOCX, PPT, PPTX, TXT, JPG, PNG
- ✅ **Limite de taille** : 10MB maximum
- ✅ **Métadonnées** : Titre, description, type
- ✅ **Sauvegarde sécurisée** avec noms de fichiers uniques

### **2. Pour les Étudiants**
- ✅ **Accès via l'emploi du temps** : Clic sur un cours → "Voir Documents"
- ✅ **Interface moderne** avec cartes de documents
- ✅ **Téléchargement direct** des documents
- ✅ **Informations détaillées** : Type, taille, date, professeur
- ✅ **Contrôle d'accès** : Seuls les étudiants de la même filière/niveau
- ✅ **Design responsive** pour mobile et desktop

### **3. Sécurité et Contrôle d'Accès**
- ✅ **Authentification** : Seuls les utilisateurs connectés
- ✅ **Autorisation** : Professeurs pour leurs cours uniquement
- ✅ **Filtrage par filière/niveau** : Étudiants voient seulement leurs documents
- ✅ **Validation des fichiers** : Types et tailles autorisés
- ✅ **Stockage sécurisé** : Dossier uploads avec noms uniques

## 🗄️ **Structure de la Base de Données**

### **Table `documents`**
```sql
CREATE TABLE documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    course_id INT NOT NULL,
    professeur_id INT NOT NULL,
    titre VARCHAR(255) NOT NULL,
    description TEXT,
    type_doc ENUM('cours', 'exercice', 'correction', 'ressource') DEFAULT 'cours',
    nom_fichier VARCHAR(255) NOT NULL,
    chemin_fichier VARCHAR(500) NOT NULL,
    taille_fichier INT NOT NULL,
    date_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
    FOREIGN KEY (professeur_id) REFERENCES users(id) ON DELETE CASCADE
);
```

## 🛠️ **Routes Implémentées**

### **1. Upload de Documents (Professeur)**
- **GET** `/professeur/upload-document/<course_id>` : Page d'upload
- **POST** `/professeur/upload-document/<course_id>` : Traitement de l'upload

### **2. Consultation de Documents (Étudiant)**
- **GET** `/student/course-documents/<course_id>` : Page des documents d'un cours

### **3. Téléchargement**
- **GET** `/download-document/<document_id>` : Téléchargement sécurisé

## 📁 **Structure des Fichiers**

### **Dossier `uploads/`**
```
uploads/
├── 20250116_143022_105_34_cours.pdf
├── 20250116_143156_105_34_exercice.docx
└── 20250116_143234_105_34_correction.pdf
```

**Format des noms** : `YYYYMMDD_HHMMSS_professeur_id_course_id_nom_original`

## 🎨 **Interface Utilisateur**

### **Page d'Upload (Professeur)**
- **Design moderne** avec glassmorphism
- **Zone de drag & drop** interactive
- **Sélection de type** avec icônes colorées
- **Aperçu du fichier** avant upload
- **Validation en temps réel**

### **Page des Documents (Étudiant)**
- **Cartes de documents** avec animations
- **Badges de type** colorés
- **Informations complètes** : taille, date, professeur
- **Bouton de téléchargement** proéminent
- **Design responsive** pour tous les écrans

## 🔄 **Flux de Données**

### **1. Upload d'un Document**
```
Professeur → Page Upload → Sélection Fichier → Validation → 
Sauvegarde Fichier → Insertion BDD → Confirmation
```

### **2. Consultation des Documents**
```
Étudiant → Emploi du Temps → Clic Cours → Modal Détails → 
"Voir Documents" → Page Documents → Téléchargement
```

### **3. Téléchargement**
```
Étudiant → Clic "Télécharger" → Vérification Permissions → 
Envoi Fichier → Téléchargement Local
```

## 🚀 **Utilisation**

### **Pour les Professeurs :**
1. Aller dans l'emploi du temps
2. Cliquer sur le bouton vert "Upload Document" d'un cours
3. Glisser-déposer ou sélectionner un fichier
4. Remplir le titre et la description
5. Choisir le type de document
6. Cliquer sur "Uploader le document"

### **Pour les Étudiants :**
1. Aller dans l'emploi du temps
2. Cliquer sur un cours
3. Dans le modal, cliquer sur "Voir Documents"
4. Voir la liste des documents disponibles
5. Cliquer sur "Télécharger" pour un document

## 🔧 **Configuration Technique**

### **Types de Fichiers Autorisés**
- **Documents** : PDF, DOC, DOCX, PPT, PPTX, TXT
- **Images** : JPG, JPEG, PNG
- **Taille maximale** : 10MB

### **Types de Documents**
- **Cours** : Supports de cours, présentations
- **Exercice** : Devoirs, exercices pratiques
- **Correction** : Solutions, corrigés
- **Ressource** : Liens, références, annexes

## 📊 **Statistiques et Monitoring**

### **Métriques Disponibles**
- Nombre de documents par cours
- Taille totale des uploads
- Types de documents les plus utilisés
- Activité par professeur

## 🔒 **Sécurité**

### **Mesures Implémentées**
- **Validation des extensions** de fichiers
- **Limitation de taille** (10MB)
- **Noms de fichiers uniques** pour éviter les conflits
- **Contrôle d'accès** par filière/niveau
- **Authentification** obligatoire
- **Autorisation** par rôle et propriétaire

## 🎯 **Avantages**

### **Pour les Professeurs :**
- Partage facile de ressources
- Organisation par type de document
- Suivi des uploads
- Interface intuitive

### **Pour les Étudiants :**
- Accès centralisé aux documents
- Téléchargement rapide
- Informations détaillées
- Interface moderne

### **Pour l'Administration :**
- Contrôle d'accès granulaire
- Stockage organisé
- Traçabilité des documents
- Sécurité renforcée

## 🚀 **Évolutions Futures**

### **Fonctionnalités Prévues**
1. **Prévisualisation** des documents dans le navigateur
2. **Recherche** dans les documents
3. **Favoris** pour les étudiants
4. **Notifications** de nouveaux documents
5. **Versioning** des documents
6. **Commentaires** sur les documents
7. **Statistiques** d'utilisation
8. **Export** de listes de documents

Le système d'upload de documents est maintenant **entièrement fonctionnel** et intégré dans l'application AdsClass ! 🎉
































