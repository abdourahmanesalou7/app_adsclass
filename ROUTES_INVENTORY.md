# 📋 INVENTAIRE COMPLET DES ROUTES - ADSClass

**Date:** 30 juin 2026
**Total routes dans app.py:** 102 routes
**Routes déjà externalisées:** ~40 routes dans `/routes/*`

---

## ✅ ROUTES DÉJÀ EXTERNALISÉES (dans routes/)

### Module Academic (`routes/academic.py`)
- `/admin/filieres-modules` - Gestion filières et modules
- `/admin/filieres-modules/new` - Nouvelle filière
- `/admin/filieres/<id>/edit` - Éditer filière
- `/admin/filieres/<id>/delete` - Supprimer filière
- `/admin/modules/new` - Nouveau module
- `/admin/modules/<id>/edit` - Éditer module
- `/admin/modules/<id>/delete` - Supprimer module
- `/api/filieres` - API liste filières

### Module Admin (`routes/admin.py`)
- `/admin/roles` - Gestion rôles
- `/admin/roles/new` - Nouveau rôle
- `/admin/roles/<id>/edit` - Éditer rôle
- `/admin/roles/<id>/permissions` - Permissions du rôle
- `/admin/roles/<id>/archive` - Archiver rôle
- `/admin/permissions` - Liste permissions
- `/admin/users/<id>/role` - Assigner rôle

### Module Admissions (`routes/admissions.py`)
- `/admin/admissions` - CRM admissions
- `/admin/admissions/new` - Nouveau candidat
- `/admin/admissions/<id>/edit` - Éditer candidat
- `/admin/admissions/<id>/convert` - Convertir en étudiant
- `/admin/admissions/<id>/delete` - Supprimer candidat

### Module Attestations (`routes/attestations.py`)
- `/admin/attestations` - Gestion attestations
- `/admin/attestations/generate` - Générer attestation
- `/student/attestations` - Attestations de l'étudiant
- `/student/attestations/<id>/download` - Télécharger attestation

### Module Students (`routes/students.py`)
- `/admin/etudiants/paiements` - Gestion paiements
- `/admin/etudiant/<id>/paiement/new` - Nouveau paiement
- `/admin/paiement/<id>/edit` - Éditer paiement
- `/admin/paiement/<id>/delete` - Supprimer paiement
- `/admin/paiement/<id>/recu` - Générer reçu
- `/admin/etudiants/export` - Export liste étudiants

### Module Teachers (`routes/teachers.py`)
- `/admin/professeurs` - Liste professeurs
- `/admin/professeur/<id>/classes` - Classes du professeur
- `/admin/professeur/<id>/credentials` - Identifiants professeur
- `/admin/professeur/<id>/delete` - Supprimer professeur

### Module Grades (`routes/grades.py`)
- `/admin/notes` - Gestion notes
- `/admin/notes/import` - Importer notes
- `/admin/notes/export` - Exporter notes
- `/admin/notes/<id>/edit` - Éditer note

### Module Chatbot Student (`routes/chatbot_student.py`)
- `/student/chatbot` - Interface chatbot étudiant
- `/api/student/chatbot/message` - Envoyer message

### Module Chatbot Admin (`routes/chatbot_admin.py`)
- `/admin/chatbot` - Interface chatbot admin
- `/api/admin/chatbot/message` - Envoyer message

---

## 🔴 ROUTES À MIGRER (restantes dans app.py - 102 routes)

### 🔐 AUTHENTIFICATION (5 routes) - PRIORITÉ 1
**Domaine:** Critique
**Complexité:** Moyenne
**Dépendances:** Aucune

| Route | Méthode | Fonction | Description |
|-------|---------|----------|-------------|
| `/` | GET | `index()` | Redirection selon rôle |
| `/register` | GET/POST | `register()` | Inscription multi-rôle |
| `/login` | GET/POST | `login()` | Authentification |
| `/logout` | GET | `logout()` | Déconnexion |
| `/api/public/filieres/<school_id>` | GET | `api_public_filieres()` | API publique |

**Proposition:** → `blueprints/auth/`

---

### 👨‍💼 ADMIN - COURS (5 routes) - PRIORITÉ 2
**Domaine:** Admin
**Complexité:** Moyenne
**Dépendances:** Filieres, Professeurs

| Route | Méthode | Fonction | Description |
|-------|---------|----------|-------------|
| `/admin/home` | GET | `admin_home()` | Dashboard admin principal |
| `/admin/dashboard` | GET | `admin_dashboard()` | Ancien dashboard (doublon?) |
| `/admin/add_course` | GET/POST | `add_course()` | Ajouter cours |
| `/admin/edit_course/<id>` | GET/POST | `edit_course()` | Modifier cours |
| `/admin/delete_course/<id>` | GET | `delete_course()` | Supprimer cours |

**Proposition:** → `blueprints/admin/courses.py`

---

### 👨‍💼 ADMIN - GESTION (13 routes) - PRIORITÉ 3
**Domaine:** Admin
**Complexité:** Faible à Moyenne

| Route | Méthode | Fonction | Description |
|-------|---------|----------|-------------|
| `/admin/add_professeur` | GET/POST | `add_professeur()` | Ajouter professeur |
| `/admin/administrateurs` | GET | `admin_administrateurs()` | Liste admins |
| `/admin/administrateurs/new` | GET/POST | `admin_administrateur_new()` | Nouvel admin |

| `/admin/administrateurs/<id>/credentials/print` | GET | `admin_administrateur_credentials_print()` | Imprimer identifiants |
| `/admin/administrateurs/<id>/delete` | POST | `admin_administrateur_delete()` | Supprimer admin |
| `/admin/etudiants/inscription` | GET/POST | `inscription_etudiant()` | Inscrire étudiant |
| `/admin/depenses` | GET/POST | `admin_depenses()` | Gestion dépenses |
| `/admin/depenses/<id>/modifier` | GET/POST | `modifier_depense()` | Modifier dépense |
| `/admin/depenses/<id>/supprimer` | POST/GET | `supprimer_depense()` | Supprimer dépense |
| `/admin/depenses/<id>/imprimer` | GET | `imprimer_depense()` | Imprimer dépense |
| `/admin/filieres` | GET | `admin_filieres()` | Gestion filières (legacy?) |
| `/admin/classes` | GET | `admin_classes()` | Liste classes |
| `/admin/classes/<filiere>/<niveau>` | GET | `admin_classe_detail()` | Détail classe |
| `/admin/class_students/<filiere>/<niveau>` | GET | `admin_class_students()` | Étudiants par classe |
| `/admin/student/<id>/credentials/print` | GET | `student_credentials_print()` | Imprimer identifiants étudiant |
| `/admin/class_students/<filiere>/<niveau>/credentials/print` | GET | `class_credentials_print()` | Imprimer identifiants classe |

**Proposition:** → `blueprints/admin/management.py`

---

## 📊 RÉSUMÉ MIGRATION

### Par Domaine Fonctionnel

| Domaine | Routes dans app.py | Déjà migrées | À migrer | Complexité |
|---------|-------------------|--------------|----------|------------|
| **Auth** | 5 | 0 | 5 | Moyenne |
| **Admin** | 30 | 15 | 15 | Moyenne-Haute |
| **Student** | 13 | 0 | 13 | Moyenne |
| **Professor** | 21 | 0 | 21 | Haute |
| **API** | 15 | 0 | 15 | Faible-Moyenne |
| **Documents** | 3 | 0 | 3 | Faible |
| **Debug** | 4 | 0 | 4 | N/A |
| **TOTAL** | **102** | **40** | **102** | - |

### Ordre de Migration Recommandé

**Phase 1 - Infrastructure (Semaine 1)**
- Créer structure `blueprints/`, `services/`, `repositories/`
- Context manager DB
- Exceptions personnalisées
- BaseRepository

**Phase 2 - Pilote Documents (Semaine 2)**
- ⭐ `/download/<id>`
- ⭐ `/download-document/<id>`
- ⭐ `/cours/<id>/documents`
- **Raison:** Simple, peu de dépendances, impact limité

**Phase 3 - Auth Critique (Semaine 3)**
- `/register`
- `/login`
- `/logout`
- `/`
- **Raison:** Fondamental, pas de dépendances

**Phase 4 - APIs (Semaine 4)**
- API Notifications (2 routes)
- API Absences (2 routes)
- API Présences QR (7 routes)
- API Users (4 routes)
- **Raison:** Indépendantes, réutilisables

**Phase 5 - Student Simple (Semaine 5)**
- Profil (4 routes)
- Paiements (2 routes)
- Notes (1 route)
- Documents (1 route)
- **Raison:** Impact utilisateur direct mais faible risque

**Phase 6 - Admin Gestion (Semaine 6)**
- Cours (5 routes)
- Gestion (13 routes)
- Stats (3 routes)
- Années académiques (5 routes)
- **Raison:** Core business

**Phase 7 - Professor (Semaine 7-8)**
- Cours (10 routes)
- Présences (4 routes)
- Classes (3 routes)
- Documents (3 routes)
- **Raison:** Complexité élevée, beaucoup de logique métier

**Phase 8 - Dashboards (Semaine 9)**
- `/student/dashboard` (1 route MASSIVE)
- `/professeur/emploi-temps` (1 route MASSIVE)
- `/admin/home` (1 route complexe)
- **Raison:** Agrègent tous les services, à faire en dernier

**Phase 9 - Nettoyage (Semaine 10)**
- Supprimer routes debug
- Nettoyer doublons
- Optimisations
- Tests coverage > 80%

---

## 🎯 MÉTRIQUES D'AVANCEMENT

### Objectifs
- [ ] app.py < 500 lignes (actuellement 7413)
- [ ] 0 routes @app.route dans app.py (actuellement 102)
- [ ] Tous les domaines en blueprints
- [ ] Coverage tests > 80%
- [ ] Documentation complète

### Checklist par Phase

**Phase 1 - Infrastructure**
- [ ] Créer dossiers blueprints/, services/, repositories/, core/
- [ ] Implémenter core/database.py (context manager)
- [ ] Implémenter core/exceptions.py
- [ ] Implémenter repositories/base.py
- [ ] Tests unitaires BaseRepository

**Phase 2 - Documents**
- [ ] repositories/document_repository.py
- [ ] services/document_service.py
- [ ] blueprints/documents/routes.py
- [ ] Tests unitaires DocumentService
- [ ] Tests d'intégration routes
- [ ] Migration app.py → blueprint
- [ ] Validation production
- [ ] Suppression ancien code

**Phase 3 - Auth**
- [ ] repositories/user_repository.py
- [ ] services/auth_service.py
- [ ] blueprints/auth/routes.py
- [ ] Tests (critique - 100% coverage)
- [ ] Migration app.py → blueprint
- [ ] Validation extensive
- [ ] Suppression ancien code

**[... et ainsi de suite pour chaque phase]**

---

## ⚠️ DOUBLONS IDENTIFIÉS

### Routes en doublon à nettoyer

1. **Dashboards Admin**
   - `/admin/home` ← Principal
   - `/admin/dashboard` ← Ancien (à supprimer?)

2. **Upload Documents Professeur**
   - `/professeur/cours/<id>/upload` (GET/POST)
   - `/professeur/upload-document/<id>` (GET)
   - `/professeur/upload-document/<id>` (POST)
   - **Action:** Consolider en une seule route

3. **Download Documents**
   - `/download/<id>`
   - `/download-document/<id>`
   - **Action:** Unifier sous `/documents/download/<id>`

4. **API Admin Users**
   - `/admin/api/etudiants`
   - `/admin/api/professeurs`
   - `/admin/api/administrateurs`
   - **Action:** RESTful `/api/admin/users?role=...`

---

## 🚀 PROCHAINES ACTIONS IMMÉDIATES

### Cette semaine
1. ✅ Valider cet inventaire avec l'équipe
2. ✅ Créer branche `refactor/architecture`
3. ✅ Implémenter Phase 1 (infrastructure)
4. ✅ Commencer Phase 2 (Documents pilote)

### Semaine prochaine
1. Finaliser Documents
2. Tests complets Documents
3. Déployer Documents en staging
4. Valider avec utilisateurs
5. Si OK → Phase 3 (Auth)

---

*Inventaire complet généré le 30 juin 2026*
*À mettre à jour au fil de l'avancement*
