# 📱 Guide du Système QR Code de Présence

## 🎯 Vue d'ensemble

Le système de présence utilise des **QR codes dynamiques** pour marquer la présence des étudiants de manière sécurisée et efficace.

---

## 🔐 Sécurité Anti-Fraude

### **1. QR Code Dynamique (30 secondes)**
- Le QR code se **régénère automatiquement toutes les 30 secondes**
- Une capture d'écran devient **invalide après 30 secondes**
- Impossible de partager le QR code avec des absents

### **2. Fenêtre Temporelle (1 heure)**
- Les étudiants ont **1 heure après le début du cours** pour marquer leur présence
- Exemple : Cours à 9h00 → Présence possible jusqu'à 10h00
- Après 10h00 → QR code refusé

### **3. Signature Cryptographique**
- Chaque QR code est signé avec SHA-256
- Impossible de créer un faux QR code
- Validation côté serveur

### **4. Vérification Filière/Niveau**
- L'étudiant doit être inscrit dans la bonne filière et niveau
- Exemple : Un étudiant en "Informatique L3" ne peut pas scanner un QR code pour "Marketing L2"

---

## 📚 Pour Chaque Cours

### **Question : Est-ce qu'il faut un nouveau QR code pour chaque cours ?**

**Réponse : OUI, de 2 façons :**

#### **1. Chaque cours différent = QR code différent**
- **Mathématiques** → QR code unique avec infos du cours de Maths
- **Physique** → QR code unique avec infos du cours de Physique
- **Informatique** → QR code unique avec infos du cours d'Informatique

**Le QR code contient :**
```json
{
  "course_name": "Mathématiques Avancées",
  "professeur": "Dr. Ahmed Benali",
  "salle": "Amphi A",
  "heure_debut": "09:00",
  "heure_fin": "11:00",
  "date": "2026-01-31",
  "filiere": "Informatique",
  "niveau": "L3"
}
```

#### **2. Même cours, même jour = QR code qui change toutes les 30s**
- Le **même cours** (ex: Maths du lundi 9h-11h)
- Le QR code se **régénère toutes les 30 secondes**
- Sécurité anti-screenshot

---

## 👨‍🏫 Utilisation Professeur

### **Méthode 1 : Afficher QR à l'Entrée** (RECOMMANDÉ pour 100+ étudiants)

1. **Accéder au cours** :
   - Aller dans l'emploi du temps
   - Cliquer sur le cours
   - Choisir **"Afficher QR à l'Entrée"**

2. **Afficher le QR Code** :
   - Une nouvelle page s'ouvre avec un **grand QR code**
   - Le QR code se régénère automatiquement toutes les 30 secondes
   - Un compte à rebours indique le temps restant

3. **Options d'affichage** :
   - 📺 **Projecteur** : Projeter sur un écran à l'entrée
   - 💻 **Ordinateur** : Afficher sur un écran d'ordinateur
   - 🖨️ **Imprimer** : Imprimer et coller (moins sécurisé)
   - 📱 **Tablette** : Afficher sur une tablette

4. **Statistiques en temps réel** :
   - Nombre de présents
   - Total d'étudiants
   - Taux de présence (%)
   - Actualisation automatique toutes les 5 secondes

### **Méthode 2 : Scanner les Cartes** (Pour petits groupes)

1. **Accéder au scanner** :
   - Aller dans l'emploi du temps
   - Cliquer sur le cours
   - Choisir **"Scanner les Cartes"**

2. **Scanner les cartes QR** :
   - Démarrer le scan
   - Scanner la carte de chaque étudiant
   - Voir la liste des présents/absents en temps réel

3. **Actions rapides** :
   - **Tous Présents** : Marquer tous comme présents (1 clic)
   - **Tous Absents** : Marquer tous comme absents (1 clic)
   - **Finaliser** : Marquer les non-scannés comme absents

---

## 👨‍🎓 Utilisation Étudiant

### **Scanner le QR Code à l'Entrée**

1. **Accéder au scanner** :
   - Se connecter au dashboard étudiant
   - Cliquer sur **"Scanner QR Entrée"** (bouton vert)

2. **Scanner le QR Code** :
   - Cliquer sur "Démarrer le scan"
   - Autoriser l'accès à la caméra
   - Scanner le QR code affiché à l'entrée de la classe

3. **Confirmation** :
   - Message de succès ✅
   - Affichage des informations du cours :
     - Nom du cours
     - Professeur
     - Salle
     - Horaire
     - Date
   - Son de confirmation

4. **Erreurs possibles** :
   - ⏱️ **QR code expiré** : Scanner le nouveau QR code affiché
   - ⏰ **Hors fenêtre temporelle** : Trop tôt ou trop tard (> 1h après le début)
   - ❌ **Mauvaise filière/niveau** : Vous n'êtes pas inscrit à ce cours
   - 📅 **Mauvaise date** : Le QR code n'est pas pour aujourd'hui

---

## 📊 Workflow Recommandé

### **Scénario : Amphithéâtre de 500 Étudiants**

```
8h50 - Professeur arrive
  └─> Ouvre /professeur/display-qr?course_id=X
  └─> Clique "Plein Écran"
  └─> Projette le QR code sur l'écran à l'entrée

9h00-9h30 - Étudiants entrent
  └─> Chaque étudiant scanne le QR code en entrant
  └─> 500 étudiants scannent en 30 minutes (17/minute)
  └─> Le prof voit les stats en temps réel : "450 présents / 500 (90%)"

9h30-11h00 - Cours
  └─> Le QR code reste affiché (retardataires peuvent scanner)
  └─> Le prof fait son cours normalement

10h00 - Fin de la fenêtre de présence
  └─> Plus possible de scanner (1h après le début)
  └─> Les 50 non-scannés = absents automatiquement

11h00 - Fin du cours
  └─> Statistiques finales : 450 présents, 50 absents, 90%
```

**Temps de gestion** : 1 clic + scan automatique = **ULTRA RAPIDE**

---

## ✅ Avantages du Système

| Critère | Ancien Système | Nouveau Système QR |
|---------|---------------|-------------------|
| **Temps pour 500 étudiants** | 30-60 minutes | 30 minutes (automatique) |
| **Intervention professeur** | Constante | 1 clic au début |
| **Risque de fraude** | Élevé | Très faible |
| **Erreurs de saisie** | Fréquentes | Aucune |
| **Statistiques temps réel** | Non | Oui |
| **Scalabilité** | Limitée | Illimitée |

---

## 🔧 Informations Techniques

### **Contenu du QR Code**
Chaque QR code contient toutes les informations du cours en JSON :
- Type : `course_entrance`
- ID du cours
- Nom du cours
- Professeur
- Salle
- Horaire (début/fin)
- Date
- Filière et niveau
- Timestamp de création
- Expiration (30 secondes)
- Signature cryptographique

### **Validations Côté Serveur**
1. ✅ Signature valide
2. ✅ QR code non expiré (< 30 secondes)
3. ✅ Date correcte (aujourd'hui)
4. ✅ Fenêtre temporelle (< 1h après début)
5. ✅ Étudiant inscrit (filière/niveau)
6. ✅ Pas de doublon (présence déjà marquée)

