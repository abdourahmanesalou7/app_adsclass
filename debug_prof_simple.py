#!/usr/bin/env python3
"""
Debug simple du dashboard professeur
"""

from app import app, get_db_connection

def debug_prof_dashboard():
    """Debug du dashboard professeur"""
    with app.test_client() as client:
        # Simuler une session professeur
        with client.session_transaction() as sess:
            sess['user_id'] = 6  # ID d'Albert
            sess['role'] = 'professeur'
            sess['nom'] = 'Diompy'
            sess['prenom'] = 'Albert'
        
        # Tester la route
        response = client.get('/professeur/dashboard')
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Dashboard accessible")
            
            # Vérifier le contenu
            content = response.get_data(as_text=True)
            
            if "Bienvenue" in content:
                print("✅ Page de bienvenue trouvée")
            else:
                print("❌ Page de bienvenue non trouvée")
                
            if "cours" in content.lower():
                print("✅ Contenu 'cours' trouvé")
            else:
                print("❌ Contenu 'cours' non trouvé")
                
            if "Albert" in content:
                print("✅ Nom 'Albert' trouvé")
            else:
                print("❌ Nom 'Albert' non trouvé")
                
            # Compter les cours
            cours_count = content.count("course-card")
            print(f"📚 Nombre de cartes de cours trouvées: {cours_count}")
            
        else:
            print(f"❌ Erreur HTTP: {response.status_code}")

if __name__ == "__main__":
    debug_prof_dashboard()