# ============================================================
# NOYAU IA - Configuration + appels modeles (Groq / OpenAI / Ollama)
# ============================================================
# Module autonome : ne depend que de os / requests / json.
# Extrait de app.py sans aucune modification de logique.
# ============================================================

import os
import requests
import json

# Configuration du modèle IA
# Supporte: 'ollama' (local), 'openai', 'groq' (gratuit), ou 'none' (fallback intelligent)
# Clé Groq : définir la variable d'environnement GROQ_API_KEY (https://console.groq.com)
AI_CONFIG = {
    'provider': os.environ.get('AI_PROVIDER', 'groq'),
    'ollama_url': os.environ.get('OLLAMA_URL', 'http://localhost:11434'),
    'ollama_model': os.environ.get('OLLAMA_MODEL', 'llama3.2'),
    'openai_api_key': os.environ.get('OPENAI_API_KEY', ''),
    'openai_model': os.environ.get('OPENAI_MODEL', 'gpt-3.5-turbo'),
    'groq_api_key': os.environ.get('GROQ_API_KEY', ''),
    'groq_model': os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile'),
    'max_tokens': int(os.environ.get('AI_MAX_TOKENS', '1024')),
    'temperature': float(os.environ.get('AI_TEMPERATURE', '0.6'))
}


def _ai_http_post(url, **kwargs):
    """Requête HTTP pour les APIs IA avec contournement SSL Windows si nécessaire"""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    kwargs.setdefault('timeout', 45)
    try:
        return requests.post(url, verify=True, **kwargs)
    except requests.exceptions.SSLError:
        print("⚠️ Groq: nouvelle tentative sans vérification SSL (certificat Windows)")
        return requests.post(url, verify=False, **kwargs)


def check_ai_available():
    """Vérifier si un modèle IA est disponible"""
    try:
        if AI_CONFIG['provider'] == 'ollama':
            response = requests.get(f"{AI_CONFIG['ollama_url']}/api/tags", timeout=2)
            return response.status_code == 200
        elif AI_CONFIG['provider'] == 'openai':
            return bool(AI_CONFIG['openai_api_key'])
        elif AI_CONFIG['provider'] == 'groq':
            return bool(AI_CONFIG['groq_api_key'])
    except:
        pass
    return False


def generate_ai_response(query, context, doc_info, chat_history=None):
    """Générer une réponse avec le modèle IA - Version améliorée avec historique"""

    query_lower = query.lower()

    question_type = "général"
    if any(w in query_lower for w in ["qu'est", "quoi", "définition", "définir", "signifie"]):
        question_type = "définition"
    elif any(w in query_lower for w in ["comment", "fonctionn", "marche", "procédure", "étapes"]):
        question_type = "explication"
    elif any(w in query_lower for w in ["pourquoi", "sert", "utilité", "avantage", "intérêt"]):
        question_type = "utilité"
    elif any(w in query_lower for w in ["exemple", "concret", "cas", "illustr"]):
        question_type = "exemple"
    elif any(w in query_lower for w in ["différence", "comparer", "versus", "vs"]):
        question_type = "comparaison"
    elif any(w in query_lower for w in ["résume", "résumé", "synthèse", "points clés"]):
        question_type = "résumé"

    system_prompt = f"""Tu es AdsClass AI, l'assistant pédagogique expert de la plateforme AdsClass (école d'informatique et data science).
Tu rédiges des réponses professionnelles, claires et structurées pour des étudiants universitaires.

FORMAT DE RÉPONSE OBLIGATOIRE (Markdown):
## [Titre pertinent de la réponse]

[Introduction : 2-3 phrases qui répondent directement à la question]

### Points clés
• [Point 1 — concis et précis]
• [Point 2]
• [Point 3]
• [Point 4 si pertinent]

### En résumé
[Une phrase de synthèse mémorable]

RÈGLES STRICTES:
1. Français uniquement, ton professoral et bienveillant
2. Base-toi EXCLUSIVEMENT sur le contexte documentaire fourni ci-dessous
3. N'invente JAMAIS de faits absents du contexte
4. Pas d'emojis dans ta réponse
5. Cite les concepts techniques avec précision (noms propres, acronymes)
6. Si l'information manque dans le contexte, indique-le et suggère une reformulation

TYPE DE QUESTION: {question_type}
- définition → définition formelle dès la première phrase de l'introduction
- explication → processus logique étape par étape dans les points clés
- utilité → avantages concrets et cas d'usage
- exemple → illustration tirée du contexte
- comparaison → différences claires entre les concepts
- résumé → synthèse des idées essentielles du document"""

    user_prompt = f"""Cours: {doc_info['cours']}
Document principal: {doc_info['titre']}

Question de l'étudiant: {query}

Contexte extrait des documents PDF de cours:
---
{context[:8000]}
---

Rédige une réponse professionnelle et complète en suivant le format demandé."""

    try:
        if AI_CONFIG['provider'] == 'ollama':
            return call_ollama(system_prompt, user_prompt)
        elif AI_CONFIG['provider'] == 'openai':
            return call_openai(system_prompt, user_prompt, chat_history)
        elif AI_CONFIG['provider'] == 'groq':
            return call_groq(system_prompt, user_prompt, chat_history)
    except Exception as e:
        print(f"Erreur IA: {e}")
    return None


def call_ollama(system_prompt, user_prompt):
    """Appeler Ollama (local) pour générer une réponse"""
    try:
        response = requests.post(
            f"{AI_CONFIG['ollama_url']}/api/generate",
            json={
                'model': AI_CONFIG['ollama_model'],
                'prompt': f"{system_prompt}\n\n{user_prompt}",
                'stream': False,
                'options': {
                    'temperature': AI_CONFIG['temperature'],
                    'num_predict': AI_CONFIG['max_tokens']
                }
            },
            timeout=30
        )
        if response.status_code == 200:
            return response.json().get('response', '')
    except Exception as e:
        print(f"Erreur Ollama: {e}")
    return None


def call_groq(system_prompt, user_prompt, chat_history=None):
    """Appeler Groq API pour générer une réponse avec historique conversationnel"""
    try:
        messages = [{"role": "system", "content": system_prompt}]

        if chat_history:
            for entry in chat_history[-4:]:
                messages.append({"role": "user", "content": entry.get('query', '')[:500]})
                if entry.get('response'):
                    messages.append({"role": "assistant", "content": entry.get('response', '')[:500]})

        messages.append({"role": "user", "content": user_prompt})

        response = _ai_http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['groq_model'],
                "messages": messages,
                "max_tokens": AI_CONFIG['max_tokens'],
                "temperature": AI_CONFIG['temperature']
            }
        )
        if response.status_code == 200:
            data = response.json()
            content = data['choices'][0]['message']['content']
            print(f"✅ Groq OK ({AI_CONFIG['groq_model']}, {len(content)} chars)")
            return content
        else:
            print(f"Erreur Groq: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Erreur Groq: {e}")
    return None


def call_openai(system_prompt, user_prompt, chat_history=None):
    """Appeler OpenAI pour générer une réponse"""
    try:
        messages = [{"role": "system", "content": system_prompt}]

        if chat_history:
            for entry in chat_history[-4:]:
                messages.append({"role": "user", "content": entry.get('query', '')[:500]})
                if entry.get('response'):
                    messages.append({"role": "assistant", "content": entry.get('response', '')[:500]})

        messages.append({"role": "user", "content": user_prompt})

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['openai_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['openai_model'],
                "messages": messages,
                "max_tokens": AI_CONFIG['max_tokens'],
                "temperature": AI_CONFIG['temperature']
            },
            timeout=45
        )
        if response.status_code == 200:
            data = response.json()
            return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"Erreur OpenAI: {e}")
    return None

