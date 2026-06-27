# 📄 Guide - Système de Factures pour Étudiants

## ✅ Fonctionnalité Implémentée

Un système complet de gestion et d'impression de factures a été ajouté côté étudiant, permettant à chaque étudiant de consulter l'historique de ses paiements et d'imprimer des factures professionnelles.

---

## 🎯 Fonctionnalités Principales

### 1. **Page Factures Étudiant** (`/student/factures`)
- ✅ Affichage de toutes les informations de l'étudiant (nom, prénom, filière, niveau)
- ✅ Statistiques financières en temps réel :
  - Total payé
  - Montant dû
  - Solde restant
  - Nombre de paiements effectués
- ✅ Badge de statut (À jour / Solde restant)
- ✅ Tableau professionnel avec tous les paiements
- ✅ Bouton d'impression pour chaque paiement

### 2. **Impression de Factures** (`/student/facture/<id>/imprimer`)
- ✅ Génération de factures professionnelles au format PDF
- ✅ Réutilisation du template `recu_paiement_pro.html` existant
- ✅ Code de sécurité unique pour chaque facture
- ✅ Montant en lettres automatique
- ✅ Vérification de sécurité (l'étudiant ne peut imprimer que ses propres factures)

### 3. **Navigation Intégrée**
- ✅ Onglet "Factures" ajouté dans tous les menus étudiants :
  - Dashboard principal
  - Page Cours
  - Page Notes
  - Page Absences
  - Menu mobile (sidebar)

---

## 📁 Fichiers Modifiés/Créés

### **Backend (app.py)**
```python
# Nouvelles routes ajoutées (lignes 3378-3523)

@app.route('/student/factures')
- Affiche la page des factures avec statistiques
- Récupère tous les paiements de l'étudiant connecté
- Calcule le solde et le statut de paiement

@app.route('/student/facture/<int:paiement_id>/imprimer')
- Génère une facture professionnelle pour un paiement spécifique
- Vérifie que le paiement appartient à l'étudiant connecté
- Utilise le template recu_paiement_pro.html
```

### **Frontend (Templates)**

#### **Nouveau fichier : `templates/student_factures.html`**
- Design moderne avec glassmorphism
- Gradient background (violet/indigo)
- 4 cartes statistiques animées
- Tableau responsive avec hover effects
- Boutons d'impression stylisés

#### **Fichiers modifiés pour la navigation :**
- `templates/student_dashboard.html` (lignes 542-562, 626-642)
- `templates/student_courses.html` (lignes 399-409)
- `templates/student_grades.html` (lignes 271-281)
- `templates/student_absences.html` (lignes 191-204)

---

## 🎨 Design & UX

### **Palette de Couleurs**
- **Background** : Gradient violet-indigo (#667eea → #764ba2)
- **Cartes** : Glass effect avec backdrop-filter blur
- **Statistiques** :
  - Vert (Total payé) : #10b981
  - Bleu (Montant dû) : #3b82f6
  - Ambre (Solde) : #f59e0b
  - Rose (Paiements) : #f43f5e

### **Animations**
- Hover sur les cartes : translateY(-4px) + shadow
- Hover sur les lignes du tableau : scale(1.01)
- Boutons d'impression : gradient + shadow au hover

### **Responsive**
- Grid adaptatif : 1 colonne (mobile) → 4 colonnes (desktop)
- Tableau scrollable horizontalement sur mobile
- Navigation mobile avec sidebar

---

## 🔒 Sécurité

### **Contrôles d'Accès**
1. ✅ Vérification du rôle `etudiant` sur toutes les routes
2. ✅ Vérification que le paiement appartient à l'étudiant connecté
3. ✅ Code de sécurité MD5 unique sur chaque facture
4. ✅ Protection contre l'accès aux factures d'autres étudiants

### **Validation des Données**
- Vérification de l'existence de l'étudiant dans la base
- Vérification de l'existence du paiement
- Gestion des erreurs avec messages flash

---

## 📊 Base de Données

### **Tables Utilisées**
```sql
-- Table users (étudiants)
SELECT id, prenom, nom, email, filiere, niveau, telephone
FROM users 
WHERE id = %s

-- Table paiements
SELECT id, date, montant, moyen, observation
FROM paiements
WHERE etudiant_id = %s
ORDER BY date DESC
```

### **Statistiques Calculées**
- `total_paye` : SUM(montant) pour l'étudiant
- `nombre_paiements` : COUNT(*) pour l'étudiant
- `solde` : montant_du - total_paye
- `a_jour` : total_paye >= montant_du

---

## 🚀 Utilisation

### **Pour l'Étudiant**
1. Se connecter avec son compte étudiant
2. Cliquer sur l'onglet **"Factures"** dans le menu
3. Consulter ses statistiques de paiement
4. Cliquer sur **"Imprimer"** pour générer une facture PDF
5. La facture s'ouvre dans un nouvel onglet, prête à être imprimée ou sauvegardée

### **Navigation**
- Accessible depuis n'importe quelle page étudiant via le menu
- Icône : `fa-file-invoice-dollar`
- Retour au dashboard via le bouton flèche en haut à gauche

---

## 🎯 Avantages

### **Pour les Étudiants**
- ✅ Accès 24/7 à l'historique des paiements
- ✅ Impression illimitée de factures professionnelles
- ✅ Suivi en temps réel du solde
- ✅ Interface moderne et intuitive

### **Pour l'Administration**
- ✅ Réduction des demandes de factures
- ✅ Automatisation complète
- ✅ Traçabilité avec codes de sécurité
- ✅ Réutilisation du template existant (cohérence)

---

## 🔧 Personnalisation

### **Modifier le Montant Dû**
Dans `app.py`, ligne 3453 :
```python
montant_du = 60000  # Modifier cette valeur selon vos besoins
```

### **Modifier les Couleurs**
Dans `templates/student_factures.html`, section `<style>` :
```css
body {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
```

### **Ajouter des Champs**
Modifier la requête SQL dans `student_factures()` pour inclure d'autres informations.

---

## ✨ Résultat Final

Un système complet, professionnel et sécurisé permettant aux étudiants de :
- 📊 Consulter leurs paiements en temps réel
- 🖨️ Imprimer des factures professionnelles
- 💰 Suivre leur solde et statut de paiement
- 📱 Accéder depuis n'importe quel appareil

**Design moderne** avec glassmorphism, gradients et animations fluides, parfaitement intégré au reste de l'application AdsClass.

