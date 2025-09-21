#!/usr/bin/env python3
"""
Test du dashboard professeur
"""

import requests
import json

def test_prof_dashboard():
    """Test du dashboard professeur"""
    try:
        # Test de connexion
        response = requests.get('http://127.0.0.1:5000/professeur/dashboard')
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Dashboard accessible")
            
            # Vérifier le contenu
            content = response.text
            
            # Chercher des éléments spécifiques
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
                
        else:
            print(f"❌ Erreur HTTP: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Erreur: {e}")

if __name__ == "__main__":
    test_prof_dashboard()