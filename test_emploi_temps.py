#!/usr/bin/env python3
"""
Test de l'emploi du temps du professeur
"""

from app import app, get_db_connection

def test_emploi_temps():
    """Test de l'emploi du temps du professeur"""
    with app.test_client() as client:
        # Simuler une session professeur
        with client.session_transaction() as sess:
            sess['user_id'] = 6  # ID d'Albert
            sess['role'] = 'professeur'
            sess['nom'] = 'Diompy'
            sess['prenom'] = 'Albert'
        
        # Tester la route emploi du temps
        response = client.get('/professeur/emploi-temps')
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Emploi du temps accessible")
            
            # Vérifier le contenu
            content = response.get_data(as_text=True)
            
            if "Albert" in content:
                print("✅ Nom 'Albert' trouvé")
            else:
                print("❌ Nom 'Albert' non trouvé")
                
            if "cours" in content.lower():
                print("✅ Contenu 'cours' trouvé")
            else:
                print("❌ Contenu 'cours' non trouvé")
                
            # Compter les cours par jour
            jours = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
            for jour in jours:
                cours_count = content.count(f"{jour}")
                print(f"📅 {jour}: {cours_count} mentions")
            
            # Chercher des cours spécifiques
            cours_tests = ['Algorithmique', 'Bases de Données', 'Programmation Web', 'Sécurité']
            for cours in cours_tests:
                if cours in content:
                    print(f"✅ Cours '{cours}' trouvé")
                else:
                    print(f"❌ Cours '{cours}' non trouvé")
                    
        else:
            print(f"❌ Erreur HTTP: {response.status_code}")

if __name__ == "__main__":
    test_emploi_temps()