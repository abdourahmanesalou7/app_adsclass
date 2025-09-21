#!/usr/bin/env python3
"""
Test d'ajout de cours avec professeur via l'interface
"""

from app import app
import requests

def test_add_course_with_prof():
    """Test d'ajout de cours avec professeur"""
    with app.test_client() as client:
        # Simuler une session admin
        with client.session_transaction() as sess:
            sess['user_id'] = 1  # ID admin
            sess['role'] = 'admin'
            sess['nom'] = 'Admin'
            sess['prenom'] = 'Test'
        
        # 1. Tester l'accès à la page d'ajout de cours
        print("🔧 TEST D'AJOUT DE COURS AVEC PROFESSEUR")
        print("=" * 50)
        
        response = client.get('/admin/ajouter-cours-simple')
        print(f"1. Page d'ajout: Status {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Page d'ajout accessible")
            
            # Vérifier que la liste des professeurs est présente
            content = response.get_data(as_text=True)
            if "Albert Diompy" in content:
                print("✅ Liste des professeurs trouvée")
            else:
                print("❌ Liste des professeurs non trouvée")
        
        # 2. Tester l'ajout d'un cours avec professeur
        print("\n2. Test d'ajout de cours...")
        
        course_data = {
            'nom_cours': 'Test Cours avec Professeur',
            'filiere': 'IA',
            'professeur_nom': 'Albert Diompy',  # Nom du professeur
            'salle': 'Salle Test',
            'date_cours': '2025-09-10',
            'jour_semaine': 'Mardi',
            'heure_debut': '10:00',
            'heure_fin': '12:00',
            'start': '2025-09-10 10:00:00',
            'end': '2025-09-10 12:00:00'
        }
        
        response = client.post('/admin/ajouter-cours-simple', data=course_data)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 302:  # Redirection après succès
            print("✅ Cours créé avec succès")
            
            # 3. Vérifier que le cours a été créé avec le bon professeur
            print("\n3. Vérification du cours créé...")
            
            # Simuler une session professeur pour vérifier
            with client.session_transaction() as sess:
                sess['user_id'] = 6  # ID d'Albert
                sess['role'] = 'professeur'
                sess['nom'] = 'Diompy'
                sess['prenom'] = 'Albert'
            
            response = client.get('/professeur/dashboard')
            if response.status_code == 200:
                content = response.get_data(as_text=True)
                if "Test Cours avec Professeur" in content:
                    print("✅ Cours visible dans le dashboard professeur")
                else:
                    print("❌ Cours non visible dans le dashboard professeur")
            else:
                print(f"❌ Erreur accès dashboard: {response.status_code}")
                
        else:
            print(f"❌ Erreur création cours: {response.status_code}")
            content = response.get_data(as_text=True)
            print(f"   Contenu: {content[:200]}...")

if __name__ == "__main__":
    test_add_course_with_prof()