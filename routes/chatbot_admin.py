"""
Module Chatbot Administrateur - AdsClass
Cluster admin: NLU Groq, etats interactifs (cours/finances/notes),
profils etudiants, moteur de commandes et routes API admin.
Extrait de app.py sans aucune modification de logique.
"""

import json
from datetime import datetime
from flask import (
    session, request, jsonify, render_template,
    flash, redirect, url_for,
)
import tenant
from student_enrollment_service import (
    normaliser_niveau,
    niveau_aliases,
    filiere_aliases,
    NIVEAU_SHORT_TO_LONG,
    resolve_filiere_by_name,
)
from routes.chatbot_ai import (
    AI_CONFIG,
    _ai_http_post,
    check_ai_available,
    generate_ai_response,
    call_ollama,
    call_groq,
    call_openai,
)

# Dependances injectees par register_chatbot_admin_routes
get_db_connection = None
get_current_year_id = None
login_required = None
admin_required = None


# ============================================================
# 🤖 CHATBOT ADMINISTRATEUR INTELLIGENT
# ============================================================

# État de conversation pour la création interactive de cours
def get_course_creation_state():
    """Récupérer l'état de création de cours depuis la session"""
    return session.get('course_creation', None)

def set_course_creation_state(state):
    """Sauvegarder l'état de création de cours dans la session"""
    session['course_creation'] = state

def clear_course_creation_state():
    """Effacer l'état de création de cours"""
    if 'course_creation' in session:
        del session['course_creation']


COURSE_COMMAND_PHRASES = (
    'programmer un nouveau cours', 'programmer un cours', 'programmer cours',
    'planifier un cours', 'planifier cours', 'ajouter un cours', 'nouveau cours',
    'créer un cours', 'creer un cours', 'créer un nouveau cours', 'creer un nouveau cours',
    'ajouter seance', 'ajouter séance', 'nouvelle seance', 'nouvelle séance',
)


def admin_is_in_interactive_flow():
    """Vérifie si un flux interactif (cours, finances, notes) est en cours"""
    course_state = get_course_creation_state()
    if course_state and course_state.get('active'):
        return True
    for key in ('finance', 'notes'):
        st = get_interactive_state(key)
        if st and st.get('active'):
            return True
    return False


def _is_course_command_phrase(text):
    """Détecte une commande de déclenchement, pas une valeur de champ"""
    q = (text or '').strip().lower()
    return any(phrase in q for phrase in COURSE_COMMAND_PHRASES)


def _match_option_from_list(query, options):
    """Associe une saisie utilisateur à une option connue (filière, niveau…)"""
    import re
    if not query or not options:
        return None
    q = query.strip().lower()
    q_norm = re.sub(r'[^\w\s]', ' ', q)
    q_norm = re.sub(r'\s+', ' ', q_norm).strip()

    for opt in options:
        if opt.lower() == q or opt.lower() == q_norm:
            return opt

    partial = []
    for opt in options:
        ol = opt.lower()
        if q == ol or q in ol or ol in q:
            partial.append(opt)
        elif q_norm and (q_norm in ol or ol in q_norm):
            partial.append(opt)

    if len(partial) == 1:
        return partial[0]
    if partial:
        for opt in partial:
            if opt.lower().startswith(q) or q.startswith(opt.lower()[:3]):
                return opt
        return partial[0]

    for opt in options:
        words = re.findall(r'\w+', opt.lower())
        if q in words:
            return opt
        initials = ''.join(w[0] for w in words if w)
        if len(q) >= 2 and q.upper() == initials.upper():
            return opt

    return None


def _parse_course_date_local(query_clean, query_lower):
    """Parse une date en français (formats courants + relatifs)"""
    import re
    from datetime import date, timedelta

    mois_map = {
        'janvier': '01', 'fevrier': '02', 'février': '02', 'mars': '03', 'avril': '04',
        'mai': '05', 'juin': '06', 'juillet': '07', 'aout': '08', 'août': '08',
        'septembre': '09', 'octobre': '10', 'novembre': '11', 'decembre': '12', 'décembre': '12'
    }

    date_cours = None
    today = date.today()

    date_match = re.search(
        r'(\d{1,2})\s+(janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)(?:\s+(\d{4}))?',
        query_lower
    )
    if date_match:
        jour = date_match.group(1).zfill(2)
        mois = mois_map.get(date_match.group(2), '01')
        annee = date_match.group(3) or str(today.year)
        date_cours = f"{annee}-{mois}-{jour}"

    if not date_cours:
        date_match2 = re.search(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})', query_clean)
        if date_match2:
            jour = date_match2.group(1).zfill(2)
            mois = date_match2.group(2).zfill(2)
            annee = date_match2.group(3)
            if len(annee) == 2:
                annee = f"20{annee}"
            date_cours = f"{annee}-{mois}-{jour}"

    if not date_cours:
        q = query_lower.strip()
        if 'demain' in q and 'après' not in q and 'apres' not in q:
            date_cours = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        elif q in ("aujourd'hui", 'aujourdhui', "aujourd hui", 'today') or "aujourd'hui" in q:
            date_cours = today.strftime('%Y-%m-%d')
        elif 'après-demain' in q or 'apres-demain' in q or 'apres demain' in q:
            date_cours = (today + timedelta(days=2)).strftime('%Y-%m-%d')
        else:
            jours_idx = {
                'lundi': 0, 'mardi': 1, 'mercredi': 2, 'jeudi': 3,
                'vendredi': 4, 'samedi': 5, 'dimanche': 6
            }
            prochain = 'prochain' in q or 'prochaine' in q
            for nom_jour, idx in jours_idx.items():
                if nom_jour in q:
                    days_ahead = idx - today.weekday()
                    if prochain or days_ahead <= 0:
                        days_ahead += 7
                    if days_ahead == 0 and prochain:
                        days_ahead = 7
                    date_cours = (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
                    break

    if date_cours:
        try:
            datetime.strptime(date_cours, '%Y-%m-%d')
        except ValueError:
            date_cours = None

    return date_cours


def admin_groq_parse_course_step(step, user_input, context=None):
    """Utiliser Groq pour extraire la valeur d'une étape de création de cours"""
    if not check_ai_available() or not user_input:
        return None

    context = context or {}
    today = datetime.now().date().isoformat()

    step_hints = {
        'nom': 'Extrais UNIQUEMENT le nom du cours. Ignore toute formulation de commande (programmer, créer, planifier).',
        'filiere': f"Choisis la filière exacte la plus pertinente parmi: {context.get('filieres', [])}",
        'niveau': f"Choisis le niveau exact le plus pertinent parmi: {context.get('niveaux', [])}",
        'professeur': f"Identifie le professeur parmi: {context.get('professeurs', [])}. null si aucun ou 'aucun'.",
        'salle': 'Extrais uniquement le nom/numéro de salle (ex: E5, Amphithéâtre).',
        'date': f"Aujourd'hui = {today}. Convertis en date ISO (YYYY-MM-DD). Gère demain, jours de la semaine, formats français.",
        'heure_debut': 'Extrais l\'heure de début au format HH:MM (24h).',
        'heure_fin': 'Extrais l\'heure de fin au format HH:MM (24h).',
    }

    system_prompt = f"""Tu es l'assistant de programmation de cours AdsClass.
Étape en cours: {step}
{step_hints.get(step, '')}

Retourne UNIQUEMENT un JSON valide (pas de markdown):
{{
  "value": "<texte ou null>",
  "date_iso": "<YYYY-MM-DD ou null>",
  "professeur_id": null,
  "confidence": 0.95
}}"""

    try:
        response = _ai_http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['groq_model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input}
                ],
                "max_tokens": 200,
                "temperature": 0.1
            }
        )
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            parsed = _parse_groq_json(content)
            if parsed and float(parsed.get('confidence') or 0) >= 0.5:
                print(f"✅ Groq course step [{step}]: {parsed.get('value') or parsed.get('date_iso')}")
                return parsed
    except Exception as e:
        print(f"Erreur Groq course step: {e}")
    return None



# ============================================================
# 🔄 ÉTATS DE CONVERSATION INTERACTIFS (Finances & Notes)
# ============================================================

def get_interactive_state(key):
    """Récupérer un état interactif depuis la session"""
    return session.get(f'interactive_{key}', None)

def set_interactive_state(key, state):
    """Sauvegarder un état interactif dans la session"""
    session[f'interactive_{key}'] = state

def clear_interactive_state(key):
    """Effacer un état interactif"""
    if f'interactive_{key}' in session:
        del session[f'interactive_{key}']

def clear_all_interactive_states():
    """Effacer tous les états interactifs"""
    keys_to_delete = [k for k in session.keys() if k.startswith('interactive_')]
    for k in keys_to_delete:
        del session[k]

def handle_interactive_finance(query, user_id):
    """Gérer la consultation interactive des finances/paiements étape par étape"""
    state = get_interactive_state('finance')
    query_clean = query.strip()
    query_lower = query_clean.lower()

    # Si l'utilisateur veut annuler
    if query_lower in ['annuler', 'cancel', 'stop', 'quitter', 'sortir', 'retour']:
        clear_interactive_state('finance')
        return {
            "type": "info",
            "message": "❌ **Consultation annulée.**\n\nTapez `statut paiement` ou `finances` pour recommencer."
        }

    # Si l'utilisateur veut faire autre chose (détecter les commandes principales)
    commandes_principales = [
        'ajouter paiement', 'nouveau paiement', 'ajouter un paiement',
        'statistiques', 'stats', 'étudiants', 'etudiants',
        'professeurs', 'aide', 'help', 'cours :', 'modifier paiement',
        'imprimer recu', 'imprimer reçu', 'paiements du jour',
        'programmer un cours', 'créer un cours', 'nouveau cours',
        'notes', 'résultats', 'resultats'
    ]

    for cmd in commandes_principales:
        if cmd in query_lower:
            clear_interactive_state('finance')
            return None

    conn = get_db_connection()
    if not conn:
        return {"type": "error", "message": "Erreur de connexion à la base de données"}
    cursor = conn.cursor(dictionary=True)
    year_id = get_current_year_id()

    # ÉTAPE 1: Choisir le type de consultation
    if state.get('step') == 'choose_type':
        if '1' in query_lower or 'étudiant' in query_lower or 'etudiant' in query_lower:
            # Récupérer les filières disponibles
            cursor.execute("SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s ORDER BY filiere", (tenant.current_school_id(),))
            filieres = [r['filiere'] for r in cursor.fetchall()]
            conn.close()

            state['type'] = 'etudiant'
            state['step'] = 'choose_filiere'
            state['filieres'] = filieres
            set_interactive_state('finance', state)

            filiere_list = '\n'.join([f"  • **{f}**" for f in filieres])
            return {
                "type": "interactive",
                "message": f"📚 **Choisissez une filière:**\n\n{filiere_list}\n\n💡 Tapez le nom de la filière ou `toutes` pour voir tous les étudiants."
            }
        elif '2' in query_lower or 'filière' in query_lower or 'filiere' in query_lower:
            cursor.execute("SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s ORDER BY filiere", (tenant.current_school_id(),))
            filieres = [r['filiere'] for r in cursor.fetchall()]
            conn.close()

            state['type'] = 'filiere'
            state['step'] = 'choose_filiere'
            state['filieres'] = filieres
            set_interactive_state('finance', state)

            filiere_list = '\n'.join([f"  • **{f}**" for f in filieres])
            return {
                "type": "interactive",
                "message": f"📚 **Choisissez une filière pour voir le bilan:**\n\n{filiere_list}"
            }
        elif '3' in query_lower or 'statut' in query_lower or 'impayé' in query_lower or 'impaye' in query_lower:
            state['type'] = 'status'
            state['step'] = 'choose_status'
            set_interactive_state('finance', state)
            conn.close()
            return {
                "type": "interactive",
                "message": "💰 **Quel statut de paiement voulez-vous consulter?**\n\n  1️⃣ **Payé** - Étudiants à jour\n  2️⃣ **Partiel** - Paiements en cours\n  3️⃣ **Impayé** - Aucun paiement\n  4️⃣ **Tous** - Voir tous les statuts"
            }
        else:
            conn.close()
            return {
                "type": "interactive",
                "message": "❓ Je n'ai pas compris. Choisissez:\n\n  1️⃣ Par étudiant\n  2️⃣ Par filière\n  3️⃣ Par statut de paiement"
            }

    # ÉTAPE 2: Choisir la filière
    elif state.get('step') == 'choose_filiere':
        filieres = state.get('filieres', [])
        selected_filiere = None

        if query_lower in ['toutes', 'tous', 'all', 'tout']:
            selected_filiere = 'all'
        else:
            for f in filieres:
                if f.lower() == query_lower or f.lower() in query_lower:
                    selected_filiere = f
                    break

        if not selected_filiere:
            conn.close()
            filiere_list = ', '.join(filieres)
            return {
                "type": "interactive",
                "message": f"❓ Filière non reconnue.\n\nFilières disponibles: {filiere_list}\n\nOu tapez `toutes` pour voir tous les étudiants."
            }

        state['filiere'] = selected_filiere

        # Récupérer les niveaux
        if selected_filiere == 'all':
            cursor.execute("SELECT DISTINCT niveau FROM users WHERE role='etudiant' AND niveau IS NOT NULL AND niveau != '' AND school_id = %s ORDER BY niveau", (tenant.current_school_id(),))
        else:
            cursor.execute("SELECT DISTINCT niveau FROM users WHERE role='etudiant' AND filiere=%s AND niveau IS NOT NULL AND niveau != '' AND school_id = %s ORDER BY niveau", (selected_filiere, tenant.current_school_id()))
        niveaux = [r['niveau'] for r in cursor.fetchall()]
        conn.close()

        state['step'] = 'choose_niveau'
        state['niveaux'] = niveaux
        set_interactive_state('finance', state)

        niveau_list = '\n'.join([f"  • **{n}**" for n in niveaux])
        return {
            "type": "interactive",
            "message": f"🎓 **Choisissez un niveau:**\n\n{niveau_list}\n\n💡 Tapez le niveau ou `tous` pour voir tous les niveaux."
        }

    # ÉTAPE 3: Choisir le niveau
    elif state.get('step') == 'choose_niveau':
        niveaux = state.get('niveaux', [])
        selected_niveau = None

        if query_lower in ['tous', 'toutes', 'all', 'tout']:
            selected_niveau = 'all'
        else:
            for n in niveaux:
                if n.lower() == query_lower or n.lower() in query_lower:
                    selected_niveau = n
                    break

        if not selected_niveau:
            conn.close()
            niveau_list = ', '.join(niveaux)
            return {
                "type": "interactive",
                "message": f"❓ Niveau non reconnu.\n\nNiveaux disponibles: {niveau_list}\n\nOu tapez `tous`."
            }

        state['niveau'] = selected_niveau
        filiere = state.get('filiere')

        # Construire la requête
        query_sql = """
            SELECT u.id, u.prenom, u.nom, u.filiere, u.niveau,
                   IFNULL(SUM(p.montant), 0) as total_paye
            FROM users u
            LEFT JOIN paiements p ON p.etudiant_id = u.id
        """
        params = []
        conditions = ["u.role = 'etudiant'"]

        if year_id:
            query_sql = """
                SELECT u.id, u.prenom, u.nom, u.filiere, u.niveau,
                       IFNULL(SUM(p.montant), 0) as total_paye
                FROM users u
                LEFT JOIN paiements p ON p.etudiant_id = u.id AND p.annee_academique_id = %s
            """
            params.append(year_id)

        if filiere != 'all':
            conditions.append("u.filiere = %s")
            params.append(filiere)
        if selected_niveau != 'all':
            conditions.append("u.niveau = %s")
            params.append(selected_niveau)

        conditions.append("u.school_id = %s")
        params.append(tenant.current_school_id())

        query_sql += " WHERE " + " AND ".join(conditions)
        query_sql += " GROUP BY u.id, u.prenom, u.nom, u.filiere, u.niveau ORDER BY u.nom"

        cursor.execute(query_sql, params)
        etudiants = cursor.fetchall()
        conn.close()
        clear_interactive_state('finance')

        if not etudiants:
            return {"type": "info", "message": "📭 Aucun étudiant trouvé avec ces critères."}

        # Afficher les résultats
        total_paye = sum(float(e['total_paye']) for e in etudiants)
        nb_paye = sum(1 for e in etudiants if float(e['total_paye']) >= 500000)
        nb_partiel = sum(1 for e in etudiants if 0 < float(e['total_paye']) < 500000)
        nb_impaye = sum(1 for e in etudiants if float(e['total_paye']) == 0)

        titre = f"💰 **Statut Paiements**"
        if filiere != 'all':
            titre += f" - {filiere}"
        if selected_niveau != 'all':
            titre += f" {selected_niveau}"

        msg = f"{titre}\n\n"
        msg += f"📊 **Résumé:** {len(etudiants)} étudiant(s)\n"
        msg += f"  ✅ Payé: {nb_paye} | ⏳ Partiel: {nb_partiel} | ❌ Impayé: {nb_impaye}\n"
        msg += f"  💵 Total collecté: **{total_paye:,.0f} FCFA**\n\n"

        # Limiter l'affichage à 10 étudiants
        for i, e in enumerate(etudiants[:10]):
            paye = float(e['total_paye'])
            status = "✅" if paye >= 500000 else ("⏳" if paye > 0 else "❌")
            msg += f"{status} **{e['nom']} {e['prenom']}** - {paye:,.0f} FCFA\n"

        if len(etudiants) > 10:
            msg += f"\n... et {len(etudiants) - 10} autre(s)"

        return {
            "type": "finance",
            "message": msg,
            "buttons": [{"label": "📊 Voir détails", "url": "/admin/finance"}]
        }

    # ÉTAPE: Choisir le statut
    elif state.get('step') == 'choose_status':
        status_filter = None
        if '1' in query_lower or 'payé' in query_lower or 'paye' in query_lower:
            status_filter = 'paye'
        elif '2' in query_lower or 'partiel' in query_lower:
            status_filter = 'partiel'
        elif '3' in query_lower or 'impayé' in query_lower or 'impaye' in query_lower:
            status_filter = 'impaye'
        elif '4' in query_lower or 'tous' in query_lower or 'all' in query_lower:
            status_filter = 'all'

        if not status_filter:
            conn.close()
            return {
                "type": "interactive",
                "message": "❓ Choisissez un statut:\n\n  1️⃣ Payé\n  2️⃣ Partiel\n  3️⃣ Impayé\n  4️⃣ Tous"
            }

        # Récupérer les étudiants avec leur statut
        if year_id:
            cursor.execute("""
                SELECT u.id, u.prenom, u.nom, u.filiere, u.niveau,
                       IFNULL(SUM(p.montant), 0) as total_paye
                FROM users u
                LEFT JOIN paiements p ON p.etudiant_id = u.id AND p.annee_academique_id = %s
                WHERE u.role = 'etudiant' AND u.school_id = %s
                GROUP BY u.id, u.prenom, u.nom, u.filiere, u.niveau
                ORDER BY u.nom
            """, (year_id, tenant.current_school_id()))
        else:
            cursor.execute("""
                SELECT u.id, u.prenom, u.nom, u.filiere, u.niveau,
                       IFNULL(SUM(p.montant), 0) as total_paye
                FROM users u
                LEFT JOIN paiements p ON p.etudiant_id = u.id
                WHERE u.role = 'etudiant' AND u.school_id = %s
                GROUP BY u.id, u.prenom, u.nom, u.filiere, u.niveau
                ORDER BY u.nom
            """, (tenant.current_school_id(),))
        etudiants = cursor.fetchall()
        conn.close()
        clear_interactive_state('finance')

        # Filtrer par statut
        if status_filter == 'paye':
            etudiants = [e for e in etudiants if float(e['total_paye']) >= 500000]
            status_label = "✅ Payé"
        elif status_filter == 'partiel':
            etudiants = [e for e in etudiants if 0 < float(e['total_paye']) < 500000]
            status_label = "⏳ Partiel"
        elif status_filter == 'impaye':
            etudiants = [e for e in etudiants if float(e['total_paye']) == 0]
            status_label = "❌ Impayé"
        else:
            status_label = "📋 Tous"

        if not etudiants:
            return {"type": "info", "message": f"📭 Aucun étudiant avec le statut {status_label}."}

        total = sum(float(e['total_paye']) for e in etudiants)
        msg = f"💰 **Étudiants - {status_label}**\n\n"
        msg += f"📊 {len(etudiants)} étudiant(s) | Total: **{total:,.0f} FCFA**\n\n"

        for i, e in enumerate(etudiants[:15]):
            paye = float(e['total_paye'])
            status = "✅" if paye >= 500000 else ("⏳" if paye > 0 else "❌")
            msg += f"{status} **{e['nom']} {e['prenom']}** ({e['filiere'] or '-'} {e['niveau'] or '-'}) - {paye:,.0f} FCFA\n"

        if len(etudiants) > 15:
            msg += f"\n... et {len(etudiants) - 15} autre(s)"

        return {
            "type": "finance",
            "message": msg,
            "buttons": [{"label": "📊 Voir tous", "url": "/admin/finance"}]
        }

    conn.close()
    return None


def handle_interactive_notes(query, user_id):
    """Gérer la consultation interactive des notes étape par étape"""
    state = get_interactive_state('notes')
    query_clean = query.strip()
    query_lower = query_clean.lower()

    # Si l'utilisateur veut annuler
    if query_lower in ['annuler', 'cancel', 'stop', 'quitter', 'sortir', 'retour']:
        clear_interactive_state('notes')
        return {
            "type": "info",
            "message": "❌ **Consultation annulée.**\n\nTapez `notes` ou `résultats` pour recommencer."
        }

    # Si l'utilisateur veut faire autre chose (détecter les commandes principales)
    commandes_principales = [
        'ajouter paiement', 'nouveau paiement', 'ajouter un paiement',
        'statistiques', 'stats', 'finances', 'étudiants', 'etudiants',
        'professeurs', 'aide', 'help', 'cours :', 'modifier paiement',
        'imprimer recu', 'imprimer reçu', 'paiements du jour', 'paiements de',
        'statut paiement', 'programmer un cours', 'créer un cours', 'nouveau cours'
    ]

    for cmd in commandes_principales:
        if cmd in query_lower:
            clear_interactive_state('notes')
            return None

    conn = get_db_connection()
    if not conn:
        return {"type": "error", "message": "Erreur de connexion à la base de données"}
    cursor = conn.cursor(dictionary=True)

    # ÉTAPE 1: Choisir le type de consultation
    if state.get('step') == 'choose_type':
        if '1' in query_lower or 'étudiant' in query_lower or 'etudiant' in query_lower:
            cursor.execute("SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s ORDER BY filiere", (tenant.current_school_id(),))
            filieres = [r['filiere'] for r in cursor.fetchall()]
            conn.close()

            state['type'] = 'etudiant'
            state['step'] = 'choose_filiere'
            state['filieres'] = filieres
            set_interactive_state('notes', state)

            filiere_list = '\n'.join([f"  • **{f}**" for f in filieres])
            return {
                "type": "interactive",
                "message": f"📚 **Choisissez une filière:**\n\n{filiere_list}\n\n💡 Tapez le nom de la filière."
            }
        elif '2' in query_lower or 'filière' in query_lower or 'filiere' in query_lower or 'classe' in query_lower:
            cursor.execute("SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s ORDER BY filiere", (tenant.current_school_id(),))
            filieres = [r['filiere'] for r in cursor.fetchall()]
            conn.close()

            state['type'] = 'classe'
            state['step'] = 'choose_filiere'
            state['filieres'] = filieres
            set_interactive_state('notes', state)

            filiere_list = '\n'.join([f"  • **{f}**" for f in filieres])
            return {
                "type": "interactive",
                "message": f"📚 **Choisissez une filière:**\n\n{filiere_list}"
            }
        elif '3' in query_lower or 'matière' in query_lower or 'matiere' in query_lower or 'cours' in query_lower:
            cursor.execute("SELECT DISTINCT nom_cours FROM notes WHERE nom_cours IS NOT NULL AND school_id = %s ORDER BY nom_cours", (tenant.current_school_id(),))
            cours = [r['nom_cours'] for r in cursor.fetchall()]
            conn.close()

            if not cours:
                clear_interactive_state('notes')
                return {"type": "info", "message": "📭 Aucune note n'a encore été saisie."}

            state['type'] = 'matiere'
            state['step'] = 'choose_matiere'
            state['cours'] = cours
            set_interactive_state('notes', state)

            cours_list = '\n'.join([f"  • **{c}**" for c in cours[:15]])
            if len(cours) > 15:
                cours_list += f"\n  ... et {len(cours) - 15} autre(s)"
            return {
                "type": "interactive",
                "message": f"📖 **Choisissez une matière:**\n\n{cours_list}"
            }
        else:
            conn.close()
            return {
                "type": "interactive",
                "message": "❓ Je n'ai pas compris. Choisissez:\n\n  1️⃣ Notes d'un étudiant\n  2️⃣ Notes d'une classe (filière/niveau)\n  3️⃣ Notes par matière"
            }

    # ÉTAPE 2: Choisir la filière
    elif state.get('step') == 'choose_filiere':
        filieres = state.get('filieres', [])
        selected_filiere = None

        for f in filieres:
            if f.lower() == query_lower or f.lower() in query_lower:
                selected_filiere = f
                break

        if not selected_filiere:
            conn.close()
            filiere_list = ', '.join(filieres)
            return {
                "type": "interactive",
                "message": f"❓ Filière non reconnue.\n\nFilières disponibles: {filiere_list}"
            }

        state['filiere'] = selected_filiere

        # Récupérer les niveaux
        cursor.execute("SELECT DISTINCT niveau FROM users WHERE role='etudiant' AND filiere=%s AND niveau IS NOT NULL AND niveau != '' AND school_id = %s ORDER BY niveau", (selected_filiere, tenant.current_school_id()))
        niveaux = [r['niveau'] for r in cursor.fetchall()]
        conn.close()

        state['step'] = 'choose_niveau'
        state['niveaux'] = niveaux
        set_interactive_state('notes', state)

        niveau_list = '\n'.join([f"  • **{n}**" for n in niveaux])
        return {
            "type": "interactive",
            "message": f"🎓 **Choisissez un niveau ({selected_filiere}):**\n\n{niveau_list}"
        }

    # ÉTAPE 3: Choisir le niveau
    elif state.get('step') == 'choose_niveau':
        niveaux = state.get('niveaux', [])
        selected_niveau = None

        for n in niveaux:
            if n.lower() == query_lower or n.lower() in query_lower:
                selected_niveau = n
                break

        if not selected_niveau:
            conn.close()
            niveau_list = ', '.join(niveaux)
            return {
                "type": "interactive",
                "message": f"❓ Niveau non reconnu.\n\nNiveaux disponibles: {niveau_list}"
            }

        state['niveau'] = selected_niveau
        filiere = state.get('filiere')
        consultation_type = state.get('type')

        if consultation_type == 'etudiant':
            # Récupérer les étudiants de cette filière/niveau
            cursor.execute("""
                SELECT id, prenom, nom FROM users
                WHERE role='etudiant' AND filiere=%s AND niveau=%s AND school_id=%s
                ORDER BY nom
            """, (filiere, selected_niveau, tenant.current_school_id()))
            etudiants = cursor.fetchall()
            conn.close()

            if not etudiants:
                clear_interactive_state('notes')
                return {"type": "info", "message": f"📭 Aucun étudiant en {filiere} {selected_niveau}."}

            state['step'] = 'choose_etudiant'
            state['etudiants'] = etudiants
            set_interactive_state('notes', state)

            etu_list = '\n'.join([f"  • **{e['nom']} {e['prenom']}**" for e in etudiants[:15]])
            if len(etudiants) > 15:
                etu_list += f"\n  ... et {len(etudiants) - 15} autre(s)"
            return {
                "type": "interactive",
                "message": f"👨‍🎓 **Choisissez un étudiant ({filiere} {selected_niveau}):**\n\n{etu_list}\n\n💡 Tapez le nom de l'étudiant."
            }
        else:
            # Type = classe, afficher les moyennes de la classe
            cursor.execute("""
                SELECT u.id, u.prenom, u.nom,
                       AVG((IFNULL(n.cc1, 0) + IFNULL(n.cc2, 0) + IFNULL(n.participation, 0) + IFNULL(n.examen, 0) * 2) / 5) as moyenne
                FROM users u
                LEFT JOIN notes n ON n.etudiant_id = u.id
                WHERE u.role='etudiant' AND u.filiere=%s AND u.niveau=%s AND u.school_id=%s
                GROUP BY u.id, u.prenom, u.nom
                ORDER BY moyenne DESC
            """, (filiere, selected_niveau, tenant.current_school_id()))
            resultats = cursor.fetchall()
            conn.close()
            clear_interactive_state('notes')

            if not resultats:
                return {"type": "info", "message": f"📭 Aucun résultat pour {filiere} {selected_niveau}."}

            msg = f"📊 **Moyennes - {filiere} {selected_niveau}**\n\n"
            for i, r in enumerate(resultats[:15], 1):
                moy = float(r['moyenne']) if r['moyenne'] else 0
                emoji = "🏆" if i <= 3 else ("✅" if moy >= 10 else "⚠️")
                msg += f"{emoji} {i}. **{r['nom']} {r['prenom']}** - {moy:.2f}/20\n"

            if len(resultats) > 15:
                msg += f"\n... et {len(resultats) - 15} autre(s)"

            return {
                "type": "grades",
                "message": msg,
                "buttons": [{"label": "📝 Gérer les notes", "url": "/admin/grades"}]
            }

    # ÉTAPE 4: Choisir l'étudiant
    elif state.get('step') == 'choose_etudiant':
        etudiants = state.get('etudiants', [])
        selected_etu = None

        for e in etudiants:
            nom_complet = f"{e['nom']} {e['prenom']}".lower()
            if e['nom'].lower() in query_lower or e['prenom'].lower() in query_lower or nom_complet in query_lower:
                selected_etu = e
                break

        if not selected_etu:
            conn.close()
            return {
                "type": "interactive",
                "message": "❓ Étudiant non reconnu. Tapez le nom ou prénom de l'étudiant."
            }

        # Récupérer les notes de l'étudiant
        cursor.execute("""
            SELECT nom_cours, cc1, cc2, participation, examen, semestre
            FROM notes WHERE etudiant_id = %s ORDER BY semestre, nom_cours
        """, (selected_etu['id'],))
        notes = cursor.fetchall()
        conn.close()
        clear_interactive_state('notes')

        if not notes:
            return {
                "type": "info",
                "message": f"📭 Aucune note enregistrée pour **{selected_etu['nom']} {selected_etu['prenom']}**.",
                "buttons": [{"label": "➕ Ajouter des notes", "url": f"/admin/grades/student/{selected_etu['id']}"}]
            }

        msg = f"📝 **Notes de {selected_etu['nom']} {selected_etu['prenom']}**\n\n"
        total_moy = 0
        count = 0

        for n in notes:
            cc1 = float(n['cc1']) if n['cc1'] else 0
            cc2 = float(n['cc2']) if n['cc2'] else 0
            part = float(n['participation']) if n['participation'] else 0
            exam = float(n['examen']) if n['examen'] else 0
            moy = (cc1 + cc2 + part + exam * 2) / 5
            total_moy += moy
            count += 1

            status = "✅" if moy >= 10 else "⚠️"
            msg += f"{status} **{n['nom_cours']}** (S{n['semestre'] or 1})\n"
            msg += f"   CC1: {cc1:.0f} | CC2: {cc2:.0f} | Part: {part:.0f} | Exam: {exam:.0f} | **Moy: {moy:.2f}**\n\n"

        if count > 0:
            moyenne_gen = total_moy / count
            msg += f"📊 **Moyenne générale:** {moyenne_gen:.2f}/20"

        return {
            "type": "grades",
            "message": msg,
            "buttons": [{"label": "📝 Modifier les notes", "url": f"/admin/grades/student/{selected_etu['id']}"}]
        }

    # ÉTAPE: Choisir la matière
    elif state.get('step') == 'choose_matiere':
        cours = state.get('cours', [])
        selected_cours = None

        for c in cours:
            if c.lower() == query_lower or c.lower() in query_lower or query_lower in c.lower():
                selected_cours = c
                break

        if not selected_cours:
            conn.close()
            return {
                "type": "interactive",
                "message": "❓ Matière non reconnue. Tapez le nom de la matière."
            }

        # Récupérer les notes pour cette matière
        cursor.execute("""
            SELECT u.prenom, u.nom, u.filiere, u.niveau, n.cc1, n.cc2, n.participation, n.examen
            FROM notes n
            JOIN users u ON u.id = n.etudiant_id
            WHERE n.nom_cours = %s
            ORDER BY u.filiere, u.niveau, u.nom
        """, (selected_cours,))
        resultats = cursor.fetchall()
        conn.close()
        clear_interactive_state('notes')

        if not resultats:
            return {"type": "info", "message": f"📭 Aucune note pour **{selected_cours}**."}

        msg = f"📖 **Notes - {selected_cours}**\n\n"
        for r in resultats[:15]:
            cc1 = float(r['cc1']) if r['cc1'] else 0
            cc2 = float(r['cc2']) if r['cc2'] else 0
            part = float(r['participation']) if r['participation'] else 0
            exam = float(r['examen']) if r['examen'] else 0
            moy = (cc1 + cc2 + part + exam * 2) / 5
            status = "✅" if moy >= 10 else "⚠️"
            msg += f"{status} **{r['nom']} {r['prenom']}** ({r['filiere']} {r['niveau']}) - **{moy:.2f}/20**\n"

        if len(resultats) > 15:
            msg += f"\n... et {len(resultats) - 15} autre(s)"

        return {
            "type": "grades",
            "message": msg,
            "buttons": [{"label": "📝 Gérer les notes", "url": "/admin/grades"}]
        }

    conn.close()
    return None


def handle_interactive_course_creation(query, user_id):
    """Gérer la création interactive de cours étape par étape"""
    import re
    from datetime import date, timedelta

    state = get_course_creation_state()
    query_clean = query.strip()
    query_lower = query_clean.lower()

    # Si l'utilisateur veut annuler
    if query_lower in ['annuler', 'cancel', 'stop', 'quitter', 'sortir']:
        clear_course_creation_state()
        return {
            "type": "info",
            "message": "❌ **Création de cours annulée.**\n\nVous pouvez recommencer avec `Programmer un cours`."
        }

    # Si l'utilisateur veut faire autre chose (détecter les commandes principales)
    commandes_principales = [
        'ajouter paiement', 'nouveau paiement', 'ajouter un paiement',
        'statistiques', 'stats', 'finances', 'étudiants', 'etudiants',
        'professeurs', 'aide', 'help', 'cours :', 'modifier paiement',
        'imprimer recu', 'imprimer reçu', 'paiements du jour', 'paiements de',
        'statut paiement', 'chercher étudiant', 'chercher etudiant',
        'liste des étudiants', 'liste des etudiants', 'recettes', 'dépenses',
        'depenses', 'bilan', 'notes'
    ]

    for cmd in commandes_principales:
        if cmd in query_lower:
            clear_course_creation_state()
            return None

    conn = get_db_connection()
    if not conn:
        return {"type": "error", "message": "Erreur de connexion à la base de données"}
    cursor = conn.cursor(dictionary=True)
    jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

    # Étape 1: Demander le nom du cours
    if state['step'] == 'nom':
        if not query_clean:
            return {
                "type": "question",
                "message": "📚 **Quel est le nom du cours ?**\n\n_Exemple: Data Science, Machine Learning, Fintech..._"
            }
        if _is_course_command_phrase(query_clean):
            return {
                "type": "question",
                "message": "⚠️ Ceci est une **commande**, pas un nom de cours.\n\n"
                          "📝 **Quel est le nom du cours ?**\n\n"
                          "_Exemple: Data Science, Machine Learning, Fintech..._"
            }

        nom_cours = query_clean
        if check_ai_available():
            groq_val = admin_groq_parse_course_step('nom', query_clean)
            if groq_val and groq_val.get('value') and not _is_course_command_phrase(groq_val['value']):
                nom_cours = groq_val['value'].strip()

        state['nom_cours'] = nom_cours
        state['step'] = 'filiere'
        set_course_creation_state(state)

        cursor.execute(
            "SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND school_id = %s ORDER BY filiere",
            (tenant.current_school_id(),)
        )
        filieres = [f['filiere'] for f in cursor.fetchall() if f['filiere']]
        state['filieres'] = filieres
        set_course_creation_state(state)
        conn.close()

        filieres_list = '\n'.join([f"• {f}" for f in filieres[:10]]) if filieres else "• IA\n• CyberSécurité\n• Data Science"

        return {
            "type": "question",
            "message": f"✅ Nom: **{state['nom_cours']}**\n\n"
                      f"🎓 **Quelle filière ?**\n\n{filieres_list}"
        }

    # Étape 2: Demander la filière
    elif state['step'] == 'filiere':
        filieres = state.get('filieres') or []
        if not filieres:
            cursor.execute(
                "SELECT DISTINCT filiere FROM users WHERE role='etudiant' AND filiere IS NOT NULL AND school_id = %s ORDER BY filiere",
                (tenant.current_school_id(),)
            )
            filieres = [f['filiere'] for f in cursor.fetchall() if f['filiere']]
            state['filieres'] = filieres

        filiere = _match_option_from_list(query_clean, filieres)
        if not filiere and check_ai_available():
            groq_val = admin_groq_parse_course_step('filiere', query_clean, {'filieres': filieres})
            if groq_val and groq_val.get('value'):
                filiere = _match_option_from_list(groq_val['value'], filieres) or groq_val['value'].strip()

        if not filiere:
            if _is_course_command_phrase(query_clean):
                filiere = None
            else:
                filiere = query_clean

        if not filiere or _is_course_command_phrase(filiere):
            conn.close()
            filieres_list = '\n'.join([f"• {f}" for f in filieres[:10]]) if filieres else "• IA\n• Data Science"
            return {
                "type": "question",
                "message": f"⚠️ **Filière non reconnue.**\n\n🎓 **Quelle filière ?**\n\n{filieres_list}"
            }

        state['filiere'] = filiere
        state['step'] = 'niveau'
        set_course_creation_state(state)

        cursor.execute(
            "SELECT DISTINCT niveau FROM users WHERE role='etudiant' AND filiere LIKE %s AND niveau IS NOT NULL AND school_id = %s ORDER BY niveau",
            (f"%{filiere}%", tenant.current_school_id())
        )
        niveaux = [n['niveau'] for n in cursor.fetchall() if n['niveau']]
        state['niveaux'] = niveaux
        set_course_creation_state(state)
        conn.close()

        niveaux_list = '\n'.join([f"• {n}" for n in niveaux]) if niveaux else "• Licence 1, 2, 3\n• Master 1, 2"

        return {
            "type": "question",
            "message": f"✅ Filière: **{state['filiere']}**\n\n"
                      f"📊 **Quel niveau ?**\n\n{niveaux_list}"
        }

    # Étape 3: Demander le niveau
    elif state['step'] == 'niveau':
        niveaux = state.get('niveaux') or []
        niveau = _match_option_from_list(query_clean, niveaux)
        if not niveau and check_ai_available():
            groq_val = admin_groq_parse_course_step('niveau', query_clean, {'niveaux': niveaux})
            if groq_val and groq_val.get('value'):
                niveau = _match_option_from_list(groq_val['value'], niveaux) or groq_val['value'].strip()

        if not niveau:
            if _is_course_command_phrase(query_clean):
                conn.close()
                niveaux_list = '\n'.join([f"• {n}" for n in niveaux]) if niveaux else "• Licence 1\n• Master 2"
                return {
                    "type": "question",
                    "message": f"⚠️ **Niveau non reconnu.**\n\n📊 **Quel niveau ?**\n\n{niveaux_list}"
                }
            niveau = query_clean

        state['niveau'] = niveau
        state['step'] = 'professeur'
        set_course_creation_state(state)

        cursor.execute("SELECT id, prenom, nom FROM users WHERE role='professeur' AND school_id = %s ORDER BY nom", (tenant.current_school_id(),))
        professeurs = cursor.fetchall()
        state['professeurs'] = [{'id': p['id'], 'nom': f"{p['prenom']} {p['nom']}"} for p in professeurs]
        set_course_creation_state(state)
        conn.close()

        profs_list = '\n'.join([f"• {p['prenom']} {p['nom']}" for p in professeurs[:15]])

        return {
            "type": "question",
            "message": f"✅ Niveau: **{state['niveau']}**\n\n"
                      f"👨‍🏫 **Quel professeur ?**\n\n{profs_list}\n\n_Tapez le nom ou 'aucun' si non assigné_"
        }

    # Étape 4: Demander le professeur
    elif state['step'] == 'professeur':
        if query_lower in ['aucun', 'non', 'pas de prof', 'sans professeur', '-']:
            state['professeur_id'] = None
            state['professeur_nom'] = None
        else:
            cursor.execute("SELECT id, prenom, nom FROM users WHERE role='professeur' AND school_id = %s", (tenant.current_school_id(),))
            professeurs = cursor.fetchall()

            prof_trouve = None
            for prof in professeurs:
                full = f"{prof['prenom']} {prof['nom']}".lower()
                if (prof['nom'].lower() in query_lower or prof['prenom'].lower() in query_lower
                        or query_lower in full):
                    prof_trouve = prof
                    break

            if not prof_trouve and check_ai_available():
                prof_labels = [f"{p['prenom']} {p['nom']}" for p in professeurs]
                groq_val = admin_groq_parse_course_step(
                    'professeur', query_clean, {'professeurs': prof_labels}
                )
                if groq_val and groq_val.get('value'):
                    gv = groq_val['value'].lower()
                    for prof in professeurs:
                        if prof['nom'].lower() in gv or prof['prenom'].lower() in gv:
                            prof_trouve = prof
                            break

            if prof_trouve:
                state['professeur_id'] = prof_trouve['id']
                state['professeur_nom'] = f"{prof_trouve['prenom']} {prof_trouve['nom']}"
            else:
                state['professeur_id'] = None
                state['professeur_nom'] = query_clean

        state['step'] = 'salle'
        set_course_creation_state(state)
        conn.close()

        prof_display = state['professeur_nom'] if state['professeur_nom'] else "Non assigné"

        return {
            "type": "question",
            "message": f"✅ Professeur: **{prof_display}**\n\n"
                      f"🏛️ **Quelle salle ?**\n\n_Exemple: E1, E2, Salle Info, Amphithéâtre..._"
        }

    # Étape 5: Demander la salle
    elif state['step'] == 'salle':
        salle = query_clean if query_clean else 'À définir'
        if check_ai_available() and len(query_clean) <= 30:
            groq_val = admin_groq_parse_course_step('salle', query_clean)
            if groq_val and groq_val.get('value'):
                salle = groq_val['value'].strip()

        state['salle'] = salle
        state['step'] = 'date'
        set_course_creation_state(state)
        conn.close()

        return {
            "type": "question",
            "message": f"✅ Salle: **{state['salle']}**\n\n"
                      f"📅 **Quelle date ?**\n\n"
                      f"_Formats acceptés:_\n"
                      f"• `15 Janvier 2025`\n"
                      f"• `18 juin 2026`\n"
                      f"• `15/01/2025`\n"
                      f"• `demain` / `lundi prochain`"
        }

    # Étape 6: Demander la date
    elif state['step'] == 'date':
        date_cours = _parse_course_date_local(query_clean, query_lower)

        if not date_cours and check_ai_available():
            groq_val = admin_groq_parse_course_step('date', query_clean)
            if groq_val:
                date_cours = groq_val.get('date_iso') or groq_val.get('value')
                if date_cours and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(date_cours)):
                    date_cours = _parse_course_date_local(str(date_cours), str(date_cours).lower())

        if not date_cours:
            conn.close()
            return {
                "type": "question",
                "message": "⚠️ **Date non reconnue.**\n\n"
                          "Veuillez entrer une date valide:\n"
                          "• `15 Janvier 2025`\n"
                          "• `18 juin 2026`\n"
                          "• `15/01/2025`\n"
                          "• `demain` / `lundi prochain`"
            }

        try:
            date_obj = datetime.strptime(date_cours, '%Y-%m-%d')
            jour_semaine = jours_semaine[date_obj.weekday()]
        except ValueError:
            conn.close()
            return {
                "type": "question",
                "message": "⚠️ **Date invalide.**\n\nVeuillez réessayer avec un format reconnu."
            }

        state['date_cours'] = date_cours
        state['jour_semaine'] = jour_semaine
        state['step'] = 'heure_debut'
        set_course_creation_state(state)
        conn.close()

        return {
            "type": "question",
            "message": f"✅ Date: **{jour_semaine} {date_cours}**\n\n"
                      f"🕐 **Heure de début ?**\n\n"
                      f"_Exemple: 09h, 9h30, 14:00..._"
        }

    # Étape 7: Demander l'heure de début
    elif state['step'] == 'heure_debut':
        heure_match = re.search(r'(\d{1,2})[h:.]?(\d{0,2})?', query_clean)
        heure_debut = None
        if heure_match:
            h = heure_match.group(1).zfill(2)
            m = heure_match.group(2).zfill(2) if heure_match.group(2) else '00'
            heure_debut = f"{h}:{m}"

        if not heure_debut and check_ai_available():
            groq_val = admin_groq_parse_course_step('heure_debut', query_clean)
            if groq_val and groq_val.get('value'):
                heure_debut = groq_val['value'].strip()

        if not heure_debut:
            conn.close()
            return {
                "type": "question",
                "message": "⚠️ **Heure non reconnue.**\n\nExemple: `09h`, `9h30`, `14:00`"
            }

        state['heure_debut'] = heure_debut
        state['step'] = 'heure_fin'
        set_course_creation_state(state)
        conn.close()

        return {
            "type": "question",
            "message": f"✅ Début: **{state['heure_debut']}**\n\n"
                      f"🕐 **Heure de fin ?**\n\n"
                      f"_Exemple: 11h, 12h30, 16:00..._"
        }

    # Étape 8: Demander l'heure de fin et créer le cours
    elif state['step'] == 'heure_fin':
        heure_match = re.search(r'(\d{1,2})[h:.]?(\d{0,2})?', query_clean)
        heure_fin = None
        if heure_match:
            h = heure_match.group(1).zfill(2)
            m = heure_match.group(2).zfill(2) if heure_match.group(2) else '00'
            heure_fin = f"{h}:{m}"

        if not heure_fin and check_ai_available():
            groq_val = admin_groq_parse_course_step('heure_fin', query_clean)
            if groq_val and groq_val.get('value'):
                heure_fin = groq_val['value'].strip()

        if not heure_fin:
            conn.close()
            return {
                "type": "question",
                "message": "⚠️ **Heure non reconnue.**\n\nExemple: `11h`, `12h30`, `16:00`"
            }

        state['heure_fin'] = heure_fin

        # Afficher le récapitulatif et demander confirmation
        state['step'] = 'confirmation'
        set_course_creation_state(state)
        conn.close()

        prof_display = state['professeur_nom'] if state['professeur_nom'] else "Non assigné"

        return {
            "type": "question",
            "message": f"📋 **Récapitulatif du cours:**\n\n"
                      f"📚 **Nom:** {state['nom_cours']}\n"
                      f"🎓 **Filière:** {state['filiere']}\n"
                      f"📊 **Niveau:** {state['niveau']}\n"
                      f"👨‍🏫 **Professeur:** {prof_display}\n"
                      f"🏛️ **Salle:** {state['salle']}\n"
                      f"📅 **Date:** {state['jour_semaine']} {state['date_cours']}\n"
                      f"🕐 **Horaire:** {state['heure_debut']} - {state['heure_fin']}\n\n"
                      f"✅ **Confirmer la création ?** (oui/non)"
        }

    # Étape 9: Confirmation
    elif state['step'] == 'confirmation':
        if query_lower in ['oui', 'yes', 'ok', 'confirmer', 'valider', 'o', 'y']:
            # Créer le cours
            try:
                year_id = get_current_year_id()

                start_datetime = f"{state['date_cours']} {state['heure_debut']}:00" if state.get('heure_debut') else None
                end_datetime = f"{state['date_cours']} {state['heure_fin']}:00" if state.get('heure_fin') else None

                cursor.execute('''
                    INSERT INTO courses (nom_cours, professeur_id, professeur_nom, filiere, niveau, salle,
                                        start, end, date_cours, jour_semaine, heure_debut, heure_fin, description, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (state['nom_cours'], state.get('professeur_id'), state.get('professeur_nom'),
                      state['filiere'], state['niveau'], state['salle'],
                      start_datetime, end_datetime, state['date_cours'], state['jour_semaine'],
                      state.get('heure_debut'), state.get('heure_fin'),
                      f"Cours de {state['nom_cours']} créé via chatbot", year_id, tenant.current_school_id()))

                course_id = cursor.lastrowid

                # Ajouter à l'emploi du temps du professeur
                if state.get('professeur_id'):
                    cursor.execute("""
                        INSERT IGNORE INTO emploi_temps (user_id, course_id, role, school_id)
                        VALUES (%s, %s, 'professeur', %s)
                    """, (state['professeur_id'], course_id, tenant.current_school_id()))

                # Ajouter à l'emploi du temps des étudiants
                cursor.execute("""
                    INSERT IGNORE INTO emploi_temps (user_id, course_id, role, school_id)
                    SELECT id, %s, 'etudiant', %s FROM users
                    WHERE role = 'etudiant' AND filiere LIKE %s AND niveau LIKE %s AND school_id = %s
                """, (course_id, tenant.current_school_id(), f"%{state['filiere']}%", f"%{state['niveau']}%", tenant.current_school_id()))

                conn.commit()
                conn.close()

                clear_course_creation_state()

                prof_display = state['professeur_nom'] if state['professeur_nom'] else "Non assigné"

                return {
                    "type": "success",
                    "message": f"✅ **Cours créé avec succès !**\n\n"
                              f"📚 **{state['nom_cours']}**\n"
                              f"📍 {state['filiere']} - {state['niveau']}\n"
                              f"🏛️ Salle: {state['salle']}\n"
                              f"📅 {state['jour_semaine']} {state['date_cours']}\n"
                              f"🕐 {state['heure_debut']} - {state['heure_fin']}\n"
                              f"👨‍🏫 {prof_display}\n\n"
                              f"💡 Les étudiants ont été automatiquement inscrits.",
                    "buttons": [
                        {"label": "📋 Voir les cours", "url": "/admin/courses"}
                    ]
                }

            except Exception as e:
                conn.close()
                clear_course_creation_state()
                return {
                    "type": "error",
                    "message": f"❌ Erreur lors de la création: {str(e)}"
                }
        else:
            clear_course_creation_state()
            conn.close()
            return {
                "type": "info",
                "message": "❌ **Création annulée.**\n\nVous pouvez recommencer avec `Programmer un cours`."
            }

    conn.close()
    return None



# ============================================================
# 🤖 CHATBOT ADMIN — INTÉGRATION GROQ IA
# ============================================================

_admin_chat_history = {}
_admin_session_context = {}


def admin_get_chat_history(user_id, max_entries=6):
    """Historique récent pour le contexte conversationnel"""
    return (_admin_chat_history.get(user_id) or [])[-max_entries:]


def admin_get_session_context(user_id):
    """Contexte de session (dernier étudiant mentionné, etc.)"""
    return _admin_session_context.get(user_id, {})


def admin_update_session_context(user_id, result):
    """Mémoriser le dernier étudiant discuté pour les questions de suivi"""
    data = result.get('data')
    if not isinstance(data, dict):
        return

    student = data.get('etudiant')
    if student and student.get('id'):
        _admin_session_context[user_id] = _admin_session_context.get(user_id, {})
        _admin_session_context[user_id]['last_student'] = {
            'id': student['id'],
            'prenom': student.get('prenom', ''),
            'nom': student.get('nom', ''),
            'email': student.get('email', ''),
            'filiere': student.get('filiere', ''),
            'niveau': student.get('niveau', ''),
        }
        _admin_session_context[user_id]['last_topic'] = result.get('type', 'general')


def admin_is_contextual_student_query(query):
    """Détecter une question de suivi sur l'étudiant en contexte"""
    q = query.lower().strip()
    followup_markers = (
        'il ', 'elle ', 'lui ', 'son ', 'sa ', 'ses ', "l'", 'cet étudiant',
        'cette étudiante', 'celui', 'celle', 'le même', 'la même', 'ce même'
    )
    info_markers = (
        'filière', 'filiere', 'niveau', 'email', 'mail', 'téléphone', 'telephone',
        'cours', 'inscrit', 'classe', 'promo', 'naissance', 'adresse', 'solde',
        'paiement', 'payé', 'paye', 'note', 'moyenne', 'absence', 'présence'
    )
    question_starters = ('quelle', 'quel', 'combien', 'où', 'comment', 'est-il', 'est-elle', 'a-t-il', 'a-t-elle')

    has_followup = any(m in q for m in followup_markers)
    has_info = any(m in q for m in info_markers)
    is_short = len(q.split()) <= 12 and any(q.startswith(w) or f' {w}' in q for w in question_starters)

    return has_followup or (has_info and is_short)


def admin_find_student_in_text(text):
    """Trouver un étudiant mentionné dans un texte (nom/prénom)"""
    import re
    conn = get_db_connection()
    if not conn:
        return None

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, prenom, nom, email, filiere, niveau FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
    etudiants = cursor.fetchall()
    conn.close()

    text_lower = text.lower()
    best, best_score = None, 0

    for e in etudiants:
        nom, prenom = (e['nom'] or '').lower(), (e['prenom'] or '').lower()
        full = f"{prenom} {nom}".strip()
        score = 0
        if full and full in text_lower:
            score += 20
        if nom and len(nom) > 2 and nom in text_lower:
            score += 10
        if prenom and len(prenom) > 2 and prenom in text_lower:
            score += 8
        if score > best_score:
            best_score, best = score, e

    return best if best_score >= 8 else None


def admin_fetch_student_full_profile(student_id):
    """Récupérer le profil complet d'un étudiant depuis la base"""
    conn = get_db_connection()
    if not conn:
        return None

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, prenom, nom, email, filiere, niveau, classe, telephone "
        "FROM users WHERE id = %s AND role = 'etudiant' AND school_id = %s",
        (student_id, tenant.current_school_id())
    )
    student = cursor.fetchone()
    if not student:
        conn.close()
        return None

    cursor.execute(
        "SELECT id, date, montant, moyen, observation FROM paiements "
        "WHERE etudiant_id = %s ORDER BY date DESC LIMIT 10",
        (student_id,)
    )
    paiements = cursor.fetchall()

    cursor.execute(
        "SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE etudiant_id = %s",
        (student_id,)
    )
    total_paye = float(cursor.fetchone()['total'] or 0)

    cursor.execute("""
        SELECT DISTINCT c.nom_cours, c.filiere, c.niveau, c.salle
        FROM emploi_temps et
        JOIN courses c ON et.course_id = c.id
        WHERE et.user_id = %s AND et.role = 'etudiant'
    """, (student_id,))
    cours = cursor.fetchall()

    cursor.execute(
        "SELECT nom_cours, CC1, CC2, Participation, Examen FROM notes WHERE etudiant_id = %s",
        (student_id,)
    )
    notes = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN statut='present' THEN 1 ELSE 0 END) as presents,
               SUM(CASE WHEN statut='absent' THEN 1 ELSE 0 END) as absents
        FROM presences WHERE etudiant_id = %s
    """, (student_id,))
    presence_row = cursor.fetchone() or {}
    conn.close()

    montant_du = 60000
    return {
        'student': student,
        'paiements': paiements,
        'total_paye': total_paye,
        'solde': montant_du - total_paye,
        'montant_du': montant_du,
        'cours': cours,
        'notes': notes,
        'presences': presence_row,
    }


def admin_format_profile_for_ai(profile):
    """Formater le profil étudiant en texte pour Groq"""
    s = profile['student']
    lines = [
        f"Étudiant: {s['prenom']} {s['nom']}",
        f"Email: {s.get('email') or 'N/A'}",
        f"Téléphone: {s.get('telephone') or 'N/A'}",
        f"Filière: {s.get('filiere') or 'N/A'}",
        f"Niveau: {s.get('niveau') or 'N/A'}",
        f"Classe: {s.get('classe') or 'N/A'}",
        f"Total payé: {profile['total_paye']:,.0f} FCFA / {profile['montant_du']:,.0f} FCFA attendus",
        f"Solde dû: {profile['solde']:,.0f} FCFA",
    ]

    if profile['cours']:
        lines.append("Cours inscrits:")
        for c in profile['cours']:
            lines.append(f"  - {c['nom_cours']} ({c.get('filiere') or ''} {c.get('niveau') or ''})")

    if profile['paiements']:
        lines.append("Derniers paiements:")
        for p in profile['paiements'][:5]:
            lines.append(f"  - {float(p['montant']):,.0f} FCFA le {p['date']} ({p.get('moyen') or 'N/A'})")

    if profile['notes']:
        lines.append("Notes:")
        for n in profile['notes']:
            vals = [n.get('CC1'), n.get('CC2'), n.get('Participation'), n.get('Examen')]
            vals = [v for v in vals if v is not None]
            moy = sum(vals) / len(vals) if vals else 0
            lines.append(f"  - {n['nom_cours']}: CC1={n.get('CC1') or '-'}, CC2={n.get('CC2') or '-'}, "
                         f"Part={n.get('Participation') or '-'}, Exam={n.get('Examen') or '-'}, Moy={moy:.1f}/20")

    pres = profile.get('presences') or {}
    if pres.get('total'):
        lines.append(f"Présences: {pres.get('presents', 0)} présent(s), {pres.get('absents', 0)} absence(s) sur {pres['total']}")

    return '\n'.join(lines)


def admin_groq_answer_from_profile(query, profile, chat_history=None):
    """Répondre à une question admin en s'appuyant sur les données réelles de l'étudiant"""
    if not check_ai_available() or not profile:
        return None

    data_context = admin_format_profile_for_ai(profile)
    history_text = ""
    if chat_history:
        for h in chat_history[-4:]:
            history_text += f"Admin: {h.get('query', '')}\nAssistant: {h.get('response', '')[:150]}\n"

    system_prompt = """Tu es AdsClass AI Admin. L'administrateur pose une question sur un étudiant.
Réponds UNIQUEMENT à partir des données fournies (base AdsClass). N'invente rien.

FORMAT (Markdown, sans emojis):
## [Titre court]
[Réponse directe en 1-3 phrases]
### Détails
• [points pertinents si nécessaire]

Règles:
- Français professionnel
- Si la donnée est N/A, dis-le clairement
- Conserve les chiffres exacts (FCFA, dates, notes)"""

    user_prompt = f"""Historique récent:
{history_text or 'Aucun'}

Données étudiant (base AdsClass):
---
{data_context}
---

Question de l'administrateur: {query}"""

    try:
        messages = [{"role": "system", "content": system_prompt}]
        if chat_history:
            for h in chat_history[-3:]:
                if h.get('query'):
                    messages.append({"role": "user", "content": h['query']})
                if h.get('response'):
                    messages.append({"role": "assistant", "content": h['response'][:400]})
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
                "max_tokens": 600,
                "temperature": 0.3
            }
        )
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            if content and len(content) > 20:
                print(f"✅ Groq Admin réponse contextuelle ({len(content)} chars)")
                return content.strip()
    except Exception as e:
        print(f"Erreur Groq answer profile: {e}")
    return None


def _parse_groq_json(text):
    """Extraire un objet JSON depuis la réponse Groq"""
    import re
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def admin_groq_parse_intent(query, user_id=None):
    """Utiliser Groq pour comprendre l'intention et extraire les entités"""
    if not check_ai_available():
        return None

    ctx = admin_get_session_context(user_id) if user_id else {}
    last_student = ctx.get('last_student')
    history = admin_get_chat_history(user_id) if user_id else []

    context_block = ""
    if last_student:
        context_block = (
            f"\nContexte: dernier étudiant discuté = "
            f"{last_student.get('prenom')} {last_student.get('nom')} "
            f"(filière: {last_student.get('filiere') or '?'}, "
            f"niveau: {last_student.get('niveau') or '?'}). "
            f"Si l'utilisateur dit 'il', 'elle', 'sa filière', etc., utilise cet étudiant."
        )
    if history:
        recent = " | ".join(h['query'][:60] for h in history[-3:])
        context_block += f"\nHistorique récent: {recent}"

    system_prompt = f"""Tu es le routeur IA d'AdsClass Admin, plateforme de gestion scolaire.
Analyse la demande de l'administrateur et retourne UNIQUEMENT un JSON valide (pas de markdown).
{context_block}

Format:
{{
  "intent": "<intent>",
  "student_name": null,
  "filiere": null,
  "niveau": null,
  "montant": null,
  "course_name": null,
  "payment_id": null,
  "confidence": 0.95
}}

Intents valides:
statistiques, liste_etudiants, liste_professeurs, liste_cours, statut_paiement,
absences, notes, recettes, depenses, finances, ajouter_paiement, modifier_paiement,
imprimer_recu, paiements_etudiant, paiements_jour, creer_cours, programmer_cours,
creer_professeur, ajouter_depense, info_etudiant, comptage_etudiants, comptage_profs,
comptage_cours, aide, unknown

Règles:
- info_etudiant: question sur filière, email, niveau, cours, infos d'un étudiant (y compris suivi "il/elle")
- student_name: prénom et/ou nom si mentionné OU déduit du contexte
- Ne JAMAIS classer une simple valeur (ex: "Data Science", "IA", "Master 2", "E5", "demain") comme programmer_cours ou creer_cours — utilise "unknown" dans ce cas
- programmer_cours / creer_cours: uniquement si l'admin demande explicitement de programmer/créer/planifier un cours
- confidence: 0.0 à 1.0"""

    try:
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-3:]:
            if h.get('query'):
                messages.append({"role": "user", "content": h['query']})
        messages.append({"role": "user", "content": query})

        response = _ai_http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['groq_model'],
                "messages": messages,
                "max_tokens": 300,
                "temperature": 0.1
            }
        )
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            parsed = _parse_groq_json(content)
            if parsed and parsed.get('intent'):
                print(f"✅ Groq Admin intent: {parsed.get('intent')} ({parsed.get('confidence', 0)})")
                return parsed
    except Exception as e:
        print(f"Erreur Groq parse intent: {e}")
    return None


def admin_build_command_from_intent(parsed, original_query):
    """Convertir l'intention Groq en commande pour le moteur existant"""
    intent = parsed.get('intent', 'unknown')
    confidence = float(parsed.get('confidence') or 0)
    if confidence < 0.55 or intent == 'unknown':
        return None

    filiere = parsed.get('filiere') or ''
    niveau = parsed.get('niveau') or ''
    student = parsed.get('student_name') or ''
    course = parsed.get('course_name') or ''
    payment_id = parsed.get('payment_id')

    commands = {
        'statistiques': 'Afficher les statistiques',
        'liste_etudiants': f"Liste des étudiants {filiere} {niveau}".strip(),
        'liste_professeurs': 'Liste des professeurs',
        'liste_cours': f"Liste des cours {filiere} {niveau}".strip(),
        'statut_paiement': f"statut paiement de {student}".strip() if student else 'statut paiement',
        'absences': f"absences de {student}".strip() if student else 'absences',
        'notes': f"notes de {student}".strip() if student else 'notes',
        'recettes': 'recettes',
        'depenses': 'dépenses',
        'finances': 'Statistiques financières',
        'ajouter_paiement': 'Ajouter un paiement',
        'modifier_paiement': f"modifier paiement #{payment_id}" if payment_id else 'modifier paiement',
        'imprimer_recu': f"imprimer reçu #{payment_id}" if payment_id else 'imprimer reçu',
        'paiements_etudiant': f"paiements de {student}".strip() if student else 'historique paiement',
        'paiements_jour': 'paiements du jour',
        'programmer_cours': 'Programmer un cours',
        'creer_cours': f"Cours : {course}" if course else 'créer un cours',
        'creer_professeur': 'créer professeur',
        'ajouter_depense': 'ajouter dépense',
        'rechercher': f"chercher {student or course}".strip() or 'chercher étudiant',
        'comptage_etudiants': f"combien étudiants {filiere} {niveau}".strip(),
        'comptage_profs': 'combien professeurs',
        'comptage_cours': 'combien cours',
        'info_etudiant': f"info étudiant {student}".strip() if student else 'info étudiant',
        'aide': 'aide',
    }

    return commands.get(intent)


def admin_groq_enhance_message(message, response_type, original_query):
    """Reformuler la réponse en style professionnel via Groq"""
    if response_type in ('interactive', 'form', 'error'):
        return None
    if not check_ai_available() or not message:
        return None

    system_prompt = """Tu es AdsClass AI Admin, assistant professionnel pour administrateurs d'école.
Reformule le message suivant en français professionnel et structuré.

RÈGLES STRICTES:
1. Conserve EXACTEMENT tous les chiffres, montants (FCFA), dates, noms et emails
2. Utilise le format Markdown: ## Titre, ### Sous-section, puces •
3. Pas d'emojis
4. Ton concis, clair et actionnable
5. Ne invente aucune donnée
6. Maximum 250 mots"""

    try:
        response = _ai_http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['groq_model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Question admin: {original_query}\n\nMessage à reformuler:\n{message[:3500]}"}
                ],
                "max_tokens": 800,
                "temperature": 0.4
            }
        )
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            if content and len(content) > 30:
                return content.strip()
    except Exception as e:
        print(f"Erreur Groq enhance admin: {e}")
    return None


def admin_groq_handle_unknown(query):
    """Réponse IA quand la commande n'est pas reconnue"""
    if not check_ai_available():
        return None

    system_prompt = """Tu es AdsClass AI Admin. L'administrateur a posé une question que le système n'a pas comprise.
Réponds professionnellement en français (Markdown, sans emojis) en:
1. Reformulant ce qu'il veut probablement faire
2. Proposant 3 commandes concrètes parmi: statistiques, liste étudiants, ajouter paiement, programmer cours, finances, statut paiement
3. Invitant à reformuler ou taper "aide"

Maximum 150 mots."""

    try:
        response = _ai_http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG['groq_model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                "max_tokens": 400,
                "temperature": 0.5
            }
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Erreur Groq unknown admin: {e}")
    return None


def admin_resolve_student_for_query(query, user_id, groq_intent=None):
    """Résoudre l'étudiant cible: nom explicite, contexte session, ou historique"""
    if groq_intent and groq_intent.get('student_name'):
        found = admin_find_student_in_text(groq_intent['student_name'])
        if found:
            return found

    found = admin_find_student_in_text(query)
    if found:
        return found

    ctx = admin_get_session_context(user_id)
    if ctx.get('last_student'):
        return ctx['last_student']

    for h in reversed(admin_get_chat_history(user_id)):
        found = admin_find_student_in_text(h.get('query', '') + ' ' + h.get('response', ''))
        if found:
            return found

    return None


def admin_chatbot_handle(query, user_id):
    """Point d'entrée admin: Groq NLU + moteur existant + reformulation pro"""
    effective_query = query
    groq_intent = None
    history = admin_get_chat_history(user_id)
    in_interactive = admin_is_in_interactive_flow()

    # Pendant un flux interactif (création cours, finances, notes), ne pas réécrire
    # la saisie via Groq — sinon "Data Science" devient "Programmer un cours".
    if not in_interactive and check_ai_available():
        groq_intent = admin_groq_parse_intent(query, user_id)

    # Questions de suivi sur l'étudiant en contexte (ex: "Il fait quelle filière ?")
    is_contextual = not in_interactive and admin_is_contextual_student_query(query)
    is_info_intent = groq_intent and groq_intent.get('intent') == 'info_etudiant'

    if is_contextual or is_info_intent:
        student = admin_resolve_student_for_query(query, user_id, groq_intent)
        if student:
            profile = admin_fetch_student_full_profile(student['id'])
            if profile:
                answer = admin_groq_answer_from_profile(query, profile, history)
                if answer:
                    result = {
                        'type': 'info_etudiant',
                        'message': answer,
                        'ai_powered': True,
                        'model': AI_CONFIG['groq_model'],
                        'data': {'etudiant': profile['student']},
                        'groq_intent': 'info_etudiant'
                    }
                    admin_update_session_context(user_id, result)
                    _admin_chat_history.setdefault(user_id, []).append({
                        'query': query,
                        'response': answer[:200],
                        'timestamp': datetime.now().isoformat()
                    })
                    return result

    if not in_interactive and check_ai_available() and groq_intent:
        built = admin_build_command_from_intent(groq_intent, query)
        if built:
            effective_query = built
            print(f"🔄 Admin commande normalisée: {effective_query}")

    result = admin_chatbot_process_command(effective_query, user_id)

    # Mémoriser l'étudiant pour les questions de suivi
    admin_update_session_context(user_id, result)

    # Extraire étudiant du message si pas encore en contexte
    if not admin_get_session_context(user_id).get('last_student'):
        found = admin_find_student_in_text(query + ' ' + (result.get('message') or ''))
        if found:
            admin_update_session_context(user_id, {'data': {'etudiant': found}, 'type': result.get('type')})

    if result.get('type') == 'default' and check_ai_available():
        ai_help = admin_groq_handle_unknown(query)
        if ai_help:
            result['message'] = ai_help
            result['type'] = 'info'
            result['ai_powered'] = True
            result['model'] = AI_CONFIG['groq_model']
            result['groq_intent'] = groq_intent.get('intent') if groq_intent else None
            return result

    skip_enhance = result.get('type') in ('interactive', 'form', 'error', 'info_etudiant', 'question')
    if not skip_enhance and check_ai_available() and result.get('message'):
        enhanced = admin_groq_enhance_message(
            result['message'], result.get('type'), query
        )
        if enhanced:
            result['message'] = enhanced
            result['ai_powered'] = True
            result['model'] = AI_CONFIG['groq_model']

    if groq_intent:
        result['groq_intent'] = groq_intent.get('intent')

    if user_id not in _admin_chat_history:
        _admin_chat_history[user_id] = []
    _admin_chat_history[user_id].append({
        'query': query,
        'response': (result.get('message') or '')[:200],
        'timestamp': datetime.now().isoformat()
    })
    if len(_admin_chat_history[user_id]) > 20:
        _admin_chat_history[user_id] = _admin_chat_history[user_id][-20:]

    return result


def admin_chatbot_process_command(query, user_id):
    """
    Traite une commande en langage naturel pour l'administrateur
    Retourne une réponse structurée avec les données ou une action
    """
    import re

    # Vérifier si on est en mode création interactive de cours
    state = get_course_creation_state()
    if state and state.get('active'):
        result = handle_interactive_course_creation(query, user_id)
        if result:
            return result

    # Vérifier si on est en mode consultation interactive de finances
    finance_state = get_interactive_state('finance')
    if finance_state and finance_state.get('active'):
        result = handle_interactive_finance(query, user_id)
        if result:
            return result

    # Vérifier si on est en mode consultation interactive de notes
    notes_state = get_interactive_state('notes')
    if notes_state and notes_state.get('active'):
        result = handle_interactive_notes(query, user_id)
        if result:
            return result

    # Nettoyer la requête (enlever emojis et caractères spéciaux)
    query_clean = re.sub(r'[^\w\s\'-àâäéèêëïîôùûüç]', ' ', query, flags=re.UNICODE)
    query_lower = query_clean.lower().strip()
    query_original = query.lower().strip()

    conn = get_db_connection()
    if not conn:
        return {"type": "error", "message": "Erreur de connexion à la base de données"}

    cursor = conn.cursor(dictionary=True)

    # Fonction helper pour détecter des mots-clés
    def contains_any(text, keywords):
        return any(kw in text for kw in keywords)

    # Fonction pour extraire filière et niveau (résolus en forme canonique
    # à partir de la table filieres dès que possible)
    def extract_filiere_niveau(text):
        filiere = None
        niveau = None

        # Patterns -> code court ; on tente ensuite de résoudre vers le nom canonique
        filieres_patterns = [
            (r'\bcca\b', 'CCA'),
            (r'\bmarketing\s*digital\b', 'Marketing Digital'),
            (r'\bintelligence\s*artificielle\b', 'Intelligence Artificielle'),
            (r'\bbig\s*data\b', 'Big Data'),
            (r'\bdata\s*science\b', 'Data Science'),
            (r'\bcybersécurité\b|\bcybersecurite\b|\bcyber\b', 'Cybersécurité'),
            (r'\bia\b', 'Intelligence Artificielle'),
            (r'\bai\b', 'Intelligence Artificielle'),
            (r'\binformatique\b|\binfo\b', 'Informatique'),
            (r'\bmarketing\b', 'Marketing'),
            (r'\bgestion\b', 'Gestion'),
            (r'\bfinance\b', 'Finance'),
            (r'\bcomptabilité\b|\bcomptabilite\b|\bcompta\b', 'Comptabilité'),
            (r'\bcloud\b', 'Cloud'),
            (r'\bdevops\b', 'DevOps'),
            (r'\bdata\b', 'Data'),
        ]

        for pattern, val in filieres_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                row = resolve_filiere_by_name(cursor, val, tenant.current_school_id())
                filiere = row['nom'] if row else val
                break

        # Niveaux - mapper vers les valeurs de la BD (Master 2, Master 1, etc.)
        niveau_patterns = [
            (r'\bm2\b|\bmaster\s*2\b', 'Master 2'),
            (r'\bm1\b|\bmaster\s*1\b', 'Master 1'),
            (r'\bl3\b|\blicence\s*3\b', 'Licence 3'),
            (r'\bl2\b|\blicence\s*2\b', 'Licence 2'),
            (r'\bl1\b|\blicence\s*1\b', 'Licence 1'),
        ]
        for pattern, niv in niveau_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                niveau = niv
                break

        return filiere, niveau

    try:
        # === STATISTIQUES GÉNÉRALES ===
        if contains_any(query_lower, ['statistique', 'stats', 'résumé', 'dashboard', 'tableau de bord', 'overview', 'récap']):
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
            nb_etudiants = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='professeur' AND school_id = %s", (tenant.current_school_id(),))
            nb_profs = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM courses WHERE school_id = %s", (tenant.current_school_id(),))
            nb_cours = cursor.fetchone()['count']

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
            total_recettes = cursor.fetchone()['total']

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
            total_depenses = cursor.fetchone()['total']

            benefice = float(total_recettes) - float(total_depenses)

            conn.close()
            return {
                "type": "stats",
                "message": f"📊 **Statistiques AdsClass**\n\n"
                          f"👨‍🎓 **Étudiants:** {nb_etudiants}\n"
                          f"👨‍🏫 **Professeurs:** {nb_profs}\n"
                          f"📚 **Cours:** {nb_cours}\n\n"
                          f"💰 **Finances:**\n"
                          f"   • Recettes: {total_recettes:,.0f} FCFA\n"
                          f"   • Dépenses: {total_depenses:,.0f} FCFA\n"
                          f"   • Bénéfice: {benefice:,.0f} FCFA",
                "data": {
                    "etudiants": nb_etudiants,
                    "professeurs": nb_profs,
                    "cours": nb_cours,
                    "recettes": float(total_recettes),
                    "depenses": float(total_depenses),
                    "benefice": benefice
                }
            }

        # === LISTE DES ÉTUDIANTS (amélioré) ===
        elif contains_any(query_lower, ['etudiant', 'étudiant', 'eleve', 'élève', 'inscrit', 'apprenant']):
            # Extraire filière et niveau de la requête
            filiere, niveau = extract_filiere_niveau(query_lower)

            # Construire la requête SQL dynamiquement
            sql = "SELECT id, prenom, nom, email, filiere, niveau FROM users WHERE role='etudiant' AND school_id = %s"
            params = [tenant.current_school_id()]

            if filiere:
                sql += " AND LOWER(filiere) LIKE %s"
                params.append(f'%{filiere.lower()}%')

            if niveau:
                sql += " AND LOWER(niveau) LIKE %s"
                params.append(f'%{niveau.lower()}%')

            sql += " ORDER BY nom"

            if not filiere and not niveau:
                sql += " LIMIT 25"

            cursor.execute(sql, params)
            etudiants = cursor.fetchall()
            conn.close()

            if not etudiants:
                filter_desc = ""
                if filiere:
                    filter_desc += f" en {filiere}"
                if niveau:
                    filter_desc += f" {niveau}"
                return {"type": "info", "message": f"📭 Aucun étudiant trouvé{filter_desc}."}

            filter_desc = ""
            if filiere:
                filter_desc += f" - {filiere}"
            if niveau:
                filter_desc += f" {niveau}"

            msg = f"👨‍🎓 **Liste des étudiants{filter_desc}** ({len(etudiants)} trouvés)\n\n"
            for i, e in enumerate(etudiants[:20], 1):
                msg += f"{i}. **{e['prenom']} {e['nom']}**\n   📧 {e['email']} | 📚 {e['filiere'] or 'N/A'} {e['niveau'] or ''}\n\n"

            if len(etudiants) > 20:
                msg += f"... et {len(etudiants) - 20} autres étudiants"

            return {"type": "list", "message": msg, "data": etudiants}

        # === LISTE DES PROFESSEURS ===
        elif contains_any(query_lower, ['prof', 'professeur', 'enseignant', 'formateur']):
            cursor.execute("SELECT id, prenom, nom, email, specialite FROM users WHERE role='professeur' AND school_id = %s ORDER BY nom", (tenant.current_school_id(),))
            profs = cursor.fetchall()
            conn.close()

            if not profs:
                return {"type": "info", "message": "📭 Aucun professeur trouvé."}

            msg = f"👨‍🏫 **Liste des professeurs** ({len(profs)})\n\n"
            for i, p in enumerate(profs, 1):
                msg += f"{i}. **{p['prenom']} {p['nom']}**\n   📧 {p['email']} | 🎯 {p['specialite'] or 'Non spécifié'}\n\n"

            return {"type": "list", "message": msg, "data": profs}

        # === LISTE DES COURS ===
        elif contains_any(query_lower, ['cours', 'module', 'matiere', 'matière', 'formation']) and not contains_any(query_lower, ['programmer', 'planifier', 'ajouter seance', 'nouvelle seance', 'creer', 'créer']) and not (query_lower.startswith('cours :') or query_lower.startswith('cours:')):
            # Extraire filière et niveau si spécifié
            filiere, niveau = extract_filiere_niveau(query_lower)

            # Grouper par nom de cours pour éviter les doublons (séances multiples du même cours)
            sql = """
                SELECT MIN(c.id) as id, c.nom_cours, c.filiere, c.niveau,
                       GROUP_CONCAT(DISTINCT c.salle ORDER BY c.salle SEPARATOR ', ') as salle,
                       CONCAT(u.prenom, ' ', u.nom) as professeur,
                       COUNT(*) as nb_seances
                FROM courses c
                LEFT JOIN users u ON c.professeur_id = u.id
                WHERE 1=1
            """
            params = []

            if filiere:
                sql += " AND LOWER(c.filiere) LIKE %s"
                params.append(f'%{filiere.lower()}%')

            if niveau:
                sql += " AND LOWER(c.niveau) LIKE %s"
                params.append(f'%{niveau.lower()}%')

            sql += " GROUP BY c.nom_cours, c.filiere, c.niveau, u.prenom, u.nom ORDER BY c.nom_cours"

            cursor.execute(sql, params)
            cours = cursor.fetchall()
            conn.close()

            if not cours:
                filter_desc = ""
                if filiere:
                    filter_desc += f" en {filiere}"
                if niveau:
                    filter_desc += f" {niveau}"
                return {"type": "info", "message": f"📭 Aucun cours trouvé{filter_desc}."}

            filter_desc = ""
            if filiere:
                filter_desc += f" - {filiere}"
            if niveau:
                filter_desc += f" {niveau}"

            msg = f"📚 **Liste des cours{filter_desc}** ({len(cours)})\n\n"
            for i, c in enumerate(cours[:15], 1):
                seances_info = f" ({c['nb_seances']} séances)" if c['nb_seances'] > 1 else ""
                msg += f"{i}. **{c['nom_cours']}**{seances_info}\n   📍 {c['filiere'] or 'N/A'} - {c['niveau'] or ''} | 🏛️ {c['salle'] or 'Salle N/A'}\n   👨‍🏫 {c['professeur'] or 'Non assigné'}\n\n"

            if len(cours) > 15:
                msg += f"... et {len(cours) - 15} autres cours"

            return {"type": "list", "message": msg, "data": cours}

        # === STATUT DE PAIEMENT - MODE INTERACTIF ===
        # Détecte si on demande le statut/paiement de façon générale ou spécifique
        elif contains_any(query_lower, ['statut', 'paiement de', 'paiements de', 'payé', 'paye', 'doit', 'solde de', 'combien a payé', 'combien a paye', 'statut paiement', 'statut des paiements']):
            # Extraire filière et niveau
            filiere, niveau = extract_filiere_niveau(query_lower)

            # Chercher un étudiant par nom/prénom dans la requête
            cursor.execute("SELECT id, prenom, nom, filiere, niveau FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
            etudiants = cursor.fetchall()

            etudiant_trouve = None
            meilleur_score = 0

            # Extraire les mots de la requête (sans les mots-clés)
            mots_requete = query_lower.split()
            mots_a_ignorer = ['statut', 'paiement', 'paiements', 'de', 'payé', 'paye', 'doit', 'solde',
                             'combien', 'a', 'pour', 'etudiant', 'étudiant', 'le', 'la', 'les', 'du', 'des',
                             'ia', 'master', 'licence', '1', '2', '3', 'l1', 'l2', 'l3', 'm1', 'm2']
            mots_nom = [m for m in mots_requete if m.lower() not in mots_a_ignorer and len(m) > 1]

            for etudiant in etudiants:
                nom = etudiant['nom'].lower().strip()
                prenom = etudiant['prenom'].lower().strip()

                score = 0

                # Vérifier chaque mot de la requête
                for mot in mots_nom:
                    mot = mot.lower().strip()
                    # Correspondance exacte du nom (priorité haute)
                    if mot == nom:
                        score += 10
                    # Correspondance exacte du prénom
                    elif mot == prenom or mot == prenom.split()[0]:  # Premier prénom si composé
                        score += 5
                    # Correspondance partielle du nom
                    elif mot in nom or nom in mot:
                        score += 3
                    # Correspondance partielle du prénom
                    elif mot in prenom or prenom.startswith(mot):
                        score += 2

                # Bonus si filière correspond
                if filiere and etudiant['filiere'] and filiere.lower() in etudiant['filiere'].lower():
                    score += 2
                # Bonus si niveau correspond
                if niveau and etudiant['niveau'] and niveau.lower() in etudiant['niveau'].lower():
                    score += 2

                if score > meilleur_score:
                    meilleur_score = score
                    etudiant_trouve = etudiant

            if etudiant_trouve and meilleur_score >= 5:
                # Récupérer les paiements de cet étudiant
                cursor.execute("""
                    SELECT id, date, montant, moyen, observation
                    FROM paiements
                    WHERE etudiant_id = %s
                    ORDER BY date DESC
                """, (etudiant_trouve['id'],))
                paiements = cursor.fetchall()

                cursor.execute("""
                    SELECT IFNULL(SUM(montant), 0) as total
                    FROM paiements
                    WHERE etudiant_id = %s
                """, (etudiant_trouve['id'],))
                total_paye = cursor.fetchone()['total']

                conn.close()

                # Calculer le statut (montant dû = 60000 FCFA par défaut)
                montant_du = 60000
                solde = montant_du - float(total_paye)
                a_jour = float(total_paye) >= montant_du

                msg = f"💳 **Statut de paiement de {etudiant_trouve['prenom']} {etudiant_trouve['nom']}**\n"
                msg += f"📚 {etudiant_trouve['filiere'] or 'N/A'} - {etudiant_trouve['niveau'] or 'N/A'}\n\n"

                if a_jour:
                    msg += f"✅ **À JOUR** - Tous les paiements effectués\n\n"
                else:
                    msg += f"⚠️ **SOLDE DÛ:** {solde:,.0f} FCFA\n\n"

                msg += f"💰 **Total payé:** {float(total_paye):,.0f} FCFA\n"
                msg += f"📊 **Montant attendu:** {montant_du:,.0f} FCFA\n\n"

                if paiements:
                    msg += f"📜 **Historique des paiements** ({len(paiements)} paiement(s)):\n\n"
                    for p in paiements[:10]:  # Limiter à 10 paiements
                        msg += f"• {float(p['montant']):,.0f} FCFA - 📅 {p['date']} | 💳 {p['moyen'] or 'N/A'}\n"
                else:
                    msg += "❌ Aucun paiement enregistré\n"

                return {
                    "type": "paiement_etudiant",
                    "message": msg,
                    "data": {
                        "etudiant": etudiant_trouve,
                        "paiements": paiements,
                        "total_paye": float(total_paye),
                        "solde": solde,
                        "a_jour": a_jour
                    }
                }
            else:
                # Aucun étudiant trouvé -> Mode interactif
                conn.close()
                set_interactive_state('finance', {'active': True, 'step': 'choose_type'})
                return {
                    "type": "interactive",
                    "message": "💰 **Consultation des Statuts de Paiement**\n\nComment souhaitez-vous rechercher?\n\n  1️⃣ **Par étudiant** - Chercher un étudiant spécifique\n  2️⃣ **Par filière** - Voir le bilan d'une filière\n  3️⃣ **Par statut** - Voir les payés/impayés/partiels\n\n💡 Tapez le numéro ou le mot-clé (ex: `étudiant`, `filière`, `statut`)\n\n_Tapez `annuler` pour quitter._"
                }

        # === ABSENCES/PRÉSENCES D'UN ÉTUDIANT SPÉCIFIQUE ===
        elif contains_any(query_lower, ['absence', 'présence', 'presence', 'assidu', 'assiduité']):
            filiere, niveau = extract_filiere_niveau(query_lower)

            cursor.execute("SELECT id, prenom, nom, filiere, niveau FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
            etudiants = cursor.fetchall()

            etudiant_trouve = None
            meilleur_score = 0

            # Extraire les mots de la requête (sans les mots-clés)
            mots_requete = query_lower.split()
            mots_a_ignorer = ['absence', 'absences', 'présence', 'présences', 'presence', 'presences',
                             'de', 'pour', 'etudiant', 'étudiant', 'assidu', 'assiduité', 'le', 'la', 'les',
                             'ia', 'master', 'licence', '1', '2', '3', 'l1', 'l2', 'l3', 'm1', 'm2']
            mots_nom = [m for m in mots_requete if m.lower() not in mots_a_ignorer and len(m) > 1]

            for etudiant in etudiants:
                nom = etudiant['nom'].lower().strip()
                prenom = etudiant['prenom'].lower().strip()

                score = 0
                for mot in mots_nom:
                    mot = mot.lower().strip()
                    if mot == nom:
                        score += 10
                    elif mot == prenom or mot == prenom.split()[0]:
                        score += 5
                    elif mot in nom or nom in mot:
                        score += 3
                    elif mot in prenom or prenom.startswith(mot):
                        score += 2

                if filiere and etudiant['filiere'] and filiere.lower() in etudiant['filiere'].lower():
                    score += 2
                if niveau and etudiant['niveau'] and niveau.lower() in etudiant['niveau'].lower():
                    score += 2

                if score > meilleur_score:
                    meilleur_score = score
                    etudiant_trouve = etudiant

            if etudiant_trouve and meilleur_score >= 5:
                # Récupérer les présences/absences de cet étudiant
                year_id = get_current_year_id()
                if year_id:
                    cursor.execute("""
                        SELECT p.statut, p.date_cours, c.nom_cours, p.commentaire
                        FROM presences p
                        JOIN courses c ON p.course_id = c.id
                        WHERE p.etudiant_id = %s AND p.annee_academique_id = %s
                        ORDER BY p.date_cours DESC
                        LIMIT 20
                    """, (etudiant_trouve['id'], year_id))
                else:
                    cursor.execute("""
                        SELECT p.statut, p.date_cours, c.nom_cours, p.commentaire
                        FROM presences p
                        JOIN courses c ON p.course_id = c.id
                        WHERE p.etudiant_id = %s
                        ORDER BY p.date_cours DESC
                        LIMIT 20
                    """, (etudiant_trouve['id'],))
                presences = cursor.fetchall()

                # Calculer les statistiques
                if year_id:
                    cursor.execute("""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN statut = 'present' THEN 1 ELSE 0 END) as presents,
                            SUM(CASE WHEN statut = 'absent' THEN 1 ELSE 0 END) as absents,
                            SUM(CASE WHEN statut = 'retard' THEN 1 ELSE 0 END) as retards
                        FROM presences
                        WHERE etudiant_id = %s AND annee_academique_id = %s
                    """, (etudiant_trouve['id'], year_id))
                else:
                    cursor.execute("""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN statut = 'present' THEN 1 ELSE 0 END) as presents,
                            SUM(CASE WHEN statut = 'absent' THEN 1 ELSE 0 END) as absents,
                            SUM(CASE WHEN statut = 'retard' THEN 1 ELSE 0 END) as retards
                        FROM presences
                        WHERE etudiant_id = %s
                    """, (etudiant_trouve['id'],))
                stats = cursor.fetchone()

                conn.close()

                total = stats['total'] or 0
                presents = stats['presents'] or 0
                absents = stats['absents'] or 0
                retards = stats['retards'] or 0
                taux_presence = round((presents / total * 100) if total > 0 else 0, 1)

                msg = f"📋 **Assiduité de {etudiant_trouve['prenom']} {etudiant_trouve['nom']}**\n"
                msg += f"📚 {etudiant_trouve['filiere'] or 'N/A'} - {etudiant_trouve['niveau'] or 'N/A'}\n\n"

                msg += f"📊 **Statistiques:**\n"
                msg += f"   ✅ Présences: {presents}\n"
                msg += f"   ❌ Absences: {absents}\n"
                msg += f"   ⏰ Retards: {retards}\n"
                msg += f"   📈 Taux de présence: {taux_presence}%\n\n"

                if presences:
                    msg += f"📜 **Dernières présences:**\n\n"
                    for p in presences[:10]:
                        status_icon = "✅" if p['statut'] == 'present' else ("❌" if p['statut'] == 'absent' else "⏰")
                        msg += f"• {status_icon} {p['nom_cours']} - 📅 {p['date_cours']}\n"

                return {
                    "type": "presence_etudiant",
                    "message": msg,
                    "data": {
                        "etudiant": etudiant_trouve,
                        "presences": presences,
                        "stats": {"total": total, "presents": presents, "absents": absents, "retards": retards, "taux": taux_presence}
                    }
                }

        # === NOTES D'UN ÉTUDIANT - MODE INTERACTIF ===
        elif contains_any(query_lower, ['note', 'notes', 'moyenne', 'résultat', 'resultat', 'bulletin', 'relevé', 'evaluation', 'évaluation']):
            filiere, niveau = extract_filiere_niveau(query_lower)

            cursor.execute("SELECT id, prenom, nom, filiere, niveau FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
            etudiants = cursor.fetchall()

            etudiant_trouve = None
            meilleur_score = 0

            # Extraire les mots de la requête (sans les mots-clés)
            mots_requete = query_lower.split()
            mots_a_ignorer = ['note', 'notes', 'moyenne', 'résultat', 'resultat', 'bulletin', 'relevé',
                             'evaluation', 'évaluation', 'de', 'pour', 'etudiant', 'étudiant', 'le', 'la', 'les',
                             'ia', 'master', 'licence', '1', '2', '3', 'l1', 'l2', 'l3', 'm1', 'm2']
            mots_nom = [m for m in mots_requete if m.lower() not in mots_a_ignorer and len(m) > 1]

            for etudiant in etudiants:
                nom = etudiant['nom'].lower().strip()
                prenom = etudiant['prenom'].lower().strip()

                score = 0
                for mot in mots_nom:
                    mot = mot.lower().strip()
                    if mot == nom:
                        score += 10
                    elif mot == prenom or mot == prenom.split()[0]:
                        score += 5
                    elif mot in nom or nom in mot:
                        score += 3
                    elif mot in prenom or prenom.startswith(mot):
                        score += 2

                if filiere and etudiant['filiere'] and filiere.lower() in etudiant['filiere'].lower():
                    score += 2
                if niveau and etudiant['niveau'] and niveau.lower() in etudiant['niveau'].lower():
                    score += 2

                if score > meilleur_score:
                    meilleur_score = score
                    etudiant_trouve = etudiant

            if etudiant_trouve and meilleur_score >= 5:
                # Récupérer les notes de cet étudiant
                cursor.execute("""
                    SELECT nom_cours, CC1, CC2, Participation, Examen
                    FROM notes
                    WHERE etudiant_id = %s
                    ORDER BY nom_cours
                """, (etudiant_trouve['id'],))
                notes = cursor.fetchall()

                conn.close()

                msg = f"📝 **Notes de {etudiant_trouve['prenom']} {etudiant_trouve['nom']}**\n"
                msg += f"📚 {etudiant_trouve['filiere'] or 'N/A'} - {etudiant_trouve['niveau'] or 'N/A'}\n\n"

                if notes:
                    total_moyenne = 0
                    nb_cours = 0
                    for n in notes:
                        # Calculer la moyenne du cours
                        vals = [n['CC1'], n['CC2'], n['Participation'], n['Examen']]
                        vals = [v for v in vals if v is not None]
                        moyenne_cours = sum(vals) / len(vals) if vals else 0
                        total_moyenne += moyenne_cours
                        nb_cours += 1

                        msg += f"📖 **{n['nom_cours']}**\n"
                        msg += f"   CC1: {n['CC1'] or '-'} | CC2: {n['CC2'] or '-'} | Participation: {n['Participation'] or '-'} | Examen: {n['Examen'] or '-'}\n"
                        msg += f"   📊 Moyenne: {moyenne_cours:.1f}/20\n\n"

                    moyenne_generale = total_moyenne / nb_cours if nb_cours > 0 else 0
                    msg += f"🎯 **Moyenne générale:** {moyenne_generale:.2f}/20\n"
                else:
                    msg += "❌ Aucune note enregistrée\n"

                return {
                    "type": "notes_etudiant",
                    "message": msg,
                    "data": {
                        "etudiant": etudiant_trouve,
                        "notes": notes
                    },
                    "buttons": [{"label": "📝 Modifier les notes", "url": f"/admin/grades/student/{etudiant_trouve['id']}"}]
                }
            else:
                # Aucun étudiant trouvé -> Mode interactif
                conn.close()
                set_interactive_state('notes', {'active': True, 'step': 'choose_type'})
                return {
                    "type": "interactive",
                    "message": "📝 **Consultation des Notes**\n\nComment souhaitez-vous rechercher?\n\n  1️⃣ **Par étudiant** - Notes d'un étudiant spécifique\n  2️⃣ **Par classe** - Moyennes d'une filière/niveau\n  3️⃣ **Par matière** - Notes d'un cours spécifique\n\n💡 Tapez le numéro ou le mot-clé (ex: `étudiant`, `classe`, `matière`)\n\n_Tapez `annuler` pour quitter._"
                }

        # === FINANCES - RECETTES ===
        elif contains_any(query_lower, ['recette', 'revenus', 'argent recu', 'argent reçu', 'encaissement']):
            cursor.execute("""
                SELECT p.id, p.date, p.montant, p.moyen,
                       CONCAT(u.prenom, ' ', u.nom) as etudiant
                FROM paiements p
                JOIN users u ON p.etudiant_id = u.id
                WHERE p.school_id = %s
                ORDER BY p.date DESC
                LIMIT 15
            """, (tenant.current_school_id(),))
            paiements = cursor.fetchall()

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
            total = cursor.fetchone()['total']
            conn.close()

            msg = f"💵 **Derniers paiements reçus** (Total: {float(total):,.0f} FCFA)\n\n"
            for p in paiements:
                msg += f"• **{p['etudiant']}** - {float(p['montant']):,.0f} FCFA\n  📅 {p['date']} | 💳 {p['moyen'] or 'Non spécifié'}\n\n"

            return {"type": "finance", "message": msg, "data": {"paiements": paiements, "total": float(total)}}

        # === FINANCES - DÉPENSES ===
        elif contains_any(query_lower, ['depense', 'dépense', 'sortie', 'frais', 'cout', 'coût', 'charge']):
            cursor.execute("SELECT * FROM depenses WHERE school_id = %s ORDER BY date DESC LIMIT 15", (tenant.current_school_id(),))
            depenses = cursor.fetchall()

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
            total = cursor.fetchone()['total']
            conn.close()

            msg = f"💸 **Dernières dépenses** (Total: {float(total):,.0f} FCFA)\n\n"
            for d in depenses:
                msg += f"• **{d['libelle']}** - {float(d['montant']):,.0f} FCFA\n  📅 {d['date']} | 📁 {d['categorie'] or 'Autre'}\n\n"

            return {"type": "finance", "message": msg, "data": {"depenses": depenses, "total": float(total)}}

        # === FINANCES GÉNÉRALES - MODE INTERACTIF ===
        elif contains_any(query_lower, ['finance', 'argent', 'budget', 'tresorerie', 'trésorerie', 'bilan financier']):
            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
            total_recettes = cursor.fetchone()['total']

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
            total_depenses = cursor.fetchone()['total']

            benefice = float(total_recettes) - float(total_depenses)
            conn.close()

            msg = f"💰 **Bilan Financier**\n\n"
            msg += f"📈 **Recettes totales:** {float(total_recettes):,.0f} FCFA\n"
            msg += f"📉 **Dépenses totales:** {float(total_depenses):,.0f} FCFA\n"
            msg += f"💵 **Bénéfice net:** {benefice:,.0f} FCFA\n\n"
            msg += f"📋 **Actions disponibles:**\n"
            msg += f"• Tapez `statut paiement` pour consulter les paiements\n"
            msg += f"• Tapez `dépenses` pour voir les dépenses\n"
            msg += f"• Tapez `recettes` pour voir les paiements reçus"

            return {
                "type": "finance",
                "message": msg,
                "buttons": [
                    {"label": "💳 Statut paiements", "action": "statut paiement"},
                    {"label": "📊 Voir détails", "url": "/admin/finance"}
                ]
            }

        # === AJOUTER UN PAIEMENT ===
        elif contains_any(query_lower, ['ajouter paiement', 'nouveau paiement', 'enregistrer paiement', 'créer paiement', 'ajouter un paiement']):
            conn.close()
            return {
                "type": "form",
                "message": "💰 **Ajouter un nouveau paiement**\n\n"
                          "Pour ajouter un paiement, cliquez sur le bouton ci-dessous pour ouvrir le formulaire.\n\n"
                          "📝 **Informations requises:**\n"
                          "• Étudiant\n"
                          "• Date du paiement\n"
                          "• Montant (FCFA)\n"
                          "• Moyen de paiement\n"
                          "• Observation (optionnel)",
                "form": {"type": "add_payment"}
            }

        # === MODIFIER UN PAIEMENT ===
        elif contains_any(query_lower, ['modifier paiement', 'éditer paiement', 'changer paiement', 'corriger paiement']):
            # Chercher un ID de paiement dans la requête
            import re
            id_match = re.search(r'paiement\s*#?(\d+)|id\s*#?(\d+)|#(\d+)', query_lower)

            if id_match:
                paiement_id = id_match.group(1) or id_match.group(2) or id_match.group(3)
                cursor.execute("""
                    SELECT p.id, p.etudiant_id, p.date, p.montant, p.moyen, p.observation,
                           u.prenom, u.nom
                    FROM paiements p
                    JOIN users u ON p.etudiant_id = u.id
                    WHERE p.id = %s
                """, (paiement_id,))
                paiement = cursor.fetchone()
                conn.close()

                if paiement:
                    return {
                        "type": "form",
                        "message": f"✏️ **Modifier le paiement #{paiement_id}**\n\n"
                                  f"👤 **Étudiant:** {paiement['prenom']} {paiement['nom']}\n"
                                  f"📅 **Date:** {paiement['date']}\n"
                                  f"💰 **Montant:** {float(paiement['montant']):,.0f} FCFA\n"
                                  f"💳 **Moyen:** {paiement['moyen'] or 'Non spécifié'}\n\n"
                                  "Cliquez sur le bouton pour modifier ce paiement.",
                        "form": {"type": "edit_payment", "etudiant_id": paiement['etudiant_id'], "paiement_id": int(paiement_id)}
                    }
                else:
                    return {"type": "error", "message": f"❌ Paiement #{paiement_id} non trouvé."}
            else:
                # Pas d'ID spécifié, demander à l'utilisateur
                conn.close()
                return {
                    "type": "info",
                    "message": "✏️ **Modifier un paiement**\n\n"
                              "Veuillez spécifier l'ID du paiement à modifier.\n\n"
                              "**Exemple:** `modifier paiement #123`\n\n"
                              "💡 Pour trouver l'ID, consultez d'abord les paiements d'un étudiant avec:\n"
                              "`paiements de [nom étudiant]`"
                }

        # === IMPRIMER REÇU DE PAIEMENT ===
        elif contains_any(query_lower, ['imprimer reçu', 'imprimer recu', 'reçu paiement', 'recu paiement', 'imprimer le reçu', 'générer reçu']):
            import re
            id_match = re.search(r'paiement\s*#?(\d+)|id\s*#?(\d+)|#(\d+)|reçu\s*#?(\d+)|recu\s*#?(\d+)', query_lower)

            if id_match:
                paiement_id = id_match.group(1) or id_match.group(2) or id_match.group(3) or id_match.group(4) or id_match.group(5)
                cursor.execute("""
                    SELECT p.id, p.date, p.montant, p.moyen, u.prenom, u.nom
                    FROM paiements p
                    JOIN users u ON p.etudiant_id = u.id
                    WHERE p.id = %s
                """, (paiement_id,))
                paiement = cursor.fetchone()
                conn.close()

                if paiement:
                    return {
                        "type": "success",
                        "message": f"🖨️ **Reçu de paiement #{paiement_id}**\n\n"
                                  f"👤 **Étudiant:** {paiement['prenom']} {paiement['nom']}\n"
                                  f"📅 **Date:** {paiement['date']}\n"
                                  f"💰 **Montant:** {float(paiement['montant']):,.0f} FCFA\n\n"
                                  "Cliquez sur le bouton pour imprimer le reçu.",
                        "buttons": [
                            {"print": f"/admin/etudiant/paiement/{paiement_id}/recu/pro", "label": "🖨️ Imprimer le reçu"}
                        ]
                    }
                else:
                    return {"type": "error", "message": f"❌ Paiement #{paiement_id} non trouvé."}
            else:
                conn.close()
                return {
                    "type": "info",
                    "message": "🖨️ **Imprimer un reçu**\n\n"
                              "Veuillez spécifier l'ID du paiement.\n\n"
                              "**Exemple:** `imprimer reçu #123`\n\n"
                              "💡 Consultez d'abord les paiements avec:\n"
                              "`paiements de [nom étudiant]`"
                }

        # === PAIEMENTS D'UN ÉTUDIANT ===
        elif contains_any(query_lower, ['paiements de', 'paiement de', 'historique paiement']):
            import re
            # Extraire le nom de l'étudiant
            name_match = re.search(r'(?:paiements?\s+de|historique\s+paiement\s+de?)\s+(.+)', query_lower)

            if name_match:
                search_name = name_match.group(1).strip()
                cursor.execute("""
                    SELECT id, prenom, nom FROM users
                    WHERE role = 'etudiant'
                    AND (LOWER(nom) LIKE %s OR LOWER(prenom) LIKE %s OR LOWER(CONCAT(prenom, ' ', nom)) LIKE %s)
                    AND school_id = %s
                    LIMIT 5
                """, (f'%{search_name}%', f'%{search_name}%', f'%{search_name}%', tenant.current_school_id()))
                etudiants = cursor.fetchall()

                if len(etudiants) == 1:
                    etudiant = etudiants[0]
                    cursor.execute("""
                        SELECT id, date, montant, moyen, observation
                        FROM paiements
                        WHERE etudiant_id = %s
                        ORDER BY date DESC
                    """, (etudiant['id'],))
                    paiements = cursor.fetchall()

                    cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE etudiant_id = %s", (etudiant['id'],))
                    total = cursor.fetchone()['total']
                    conn.close()

                    if paiements:
                        msg = f"💰 **Paiements de {etudiant['prenom']} {etudiant['nom']}**\n"
                        msg += f"📊 **Total:** {float(total):,.0f} FCFA\n\n"

                        buttons = []
                        for p in paiements[:10]:
                            msg += f"• **#{p['id']}** - {float(p['montant']):,.0f} FCFA\n"
                            msg += f"  📅 {p['date']} | 💳 {p['moyen'] or 'N/A'}\n\n"
                            buttons.append({"print": f"/admin/etudiant/paiement/{p['id']}/recu/pro", "label": f"🖨️ Reçu #{p['id']}"})

                        return {"type": "finance", "message": msg, "buttons": buttons[:5]}
                    else:
                        return {"type": "info", "message": f"ℹ️ Aucun paiement trouvé pour {etudiant['prenom']} {etudiant['nom']}."}
                elif len(etudiants) > 1:
                    conn.close()
                    msg = "👥 **Plusieurs étudiants trouvés:**\n\n"
                    for e in etudiants:
                        msg += f"• {e['prenom']} {e['nom']}\n"
                    msg += "\n💡 Précisez le nom complet."
                    return {"type": "info", "message": msg}
                else:
                    conn.close()
                    return {"type": "warning", "message": f"⚠️ Aucun étudiant trouvé avec le nom '{search_name}'."}
            else:
                conn.close()
                return {"type": "info", "message": "💡 **Usage:** `paiements de [nom étudiant]`\n\nExemple: `paiements de Abdourahmane Salou`"}

        # === PAIEMENTS DU JOUR ===
        elif contains_any(query_lower, ['paiements du jour', 'paiement du jour', 'paiements aujourd']):
            from datetime import date as dt_date
            today = dt_date.today().strftime('%Y-%m-%d')

            cursor.execute("""
                SELECT p.id, p.date, p.montant, p.moyen, u.prenom, u.nom
                FROM paiements p
                JOIN users u ON p.etudiant_id = u.id
                WHERE p.date = %s AND p.school_id = %s
                ORDER BY p.id DESC
            """, (today, tenant.current_school_id()))
            paiements = cursor.fetchall()

            cursor.execute("SELECT IFNULL(SUM(montant), 0) as total FROM paiements WHERE date = %s AND school_id = %s", (today, tenant.current_school_id()))
            total = cursor.fetchone()['total']
            conn.close()

            if paiements:
                msg = f"📅 **Paiements du {today}**\n"
                msg += f"📊 **Total:** {float(total):,.0f} FCFA ({len(paiements)} paiement(s))\n\n"

                buttons = []
                for p in paiements:
                    msg += f"• **#{p['id']}** - {p['prenom']} {p['nom']}\n"
                    msg += f"  💰 {float(p['montant']):,.0f} FCFA | 💳 {p['moyen'] or 'N/A'}\n\n"
                    buttons.append({"print": f"/admin/etudiant/paiement/{p['id']}/recu/pro", "label": f"🖨️ #{p['id']}"})

                return {"type": "finance", "message": msg, "buttons": buttons[:5]}
            else:
                return {"type": "info", "message": f"ℹ️ Aucun paiement enregistré aujourd'hui ({today})."}

        # === CRÉER UN COURS (format simplifié) ===
        # Format: Cours : [nom] [filière] [niveau] [professeur] Salle [salle] de [heure] à [heure] le [date]
        # Exemple: Cours : Fintech IA Master 2 RIAD Salle E2 de 09h à 11h le 29 Decembre 2025
        elif query_lower.startswith('cours :') or query_lower.startswith('cours:'):
            try:
                # Extraire la partie après "Cours :"
                parts = query.split(':', 1)[1].strip() if ':' in query else ''

                if not parts:
                    conn.close()
                    return {
                        "type": "warning",
                        "message": "⚠️ **Format requis:**\n\n"
                                  "`Cours : [Nom] [Filière] [Niveau] [Professeur] Salle [Salle] de [Heure] à [Heure] le [Date]`\n\n"
                                  "**Exemple:**\n"
                                  "`Cours : Fintech IA Master 2 RIAD Salle E2 de 09h à 11h le 29 Decembre 2025`"
                    }

                # Extraire la date (formats: le 29 Decembre 2025, le 29/12/2025, le 29-12-2025)
                from datetime import datetime, date
                date_cours = None
                jour_semaine = None

                # Format: le 29 Decembre 2025 ou le 29 décembre 2025
                mois_map = {
                    'janvier': '01', 'fevrier': '02', 'février': '02', 'mars': '03', 'avril': '04',
                    'mai': '05', 'juin': '06', 'juillet': '07', 'aout': '08', 'août': '08',
                    'septembre': '09', 'octobre': '10', 'novembre': '11', 'decembre': '12', 'décembre': '12'
                }
                jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

                date_match = re.search(r'le\s+(\d{1,2})\s+(janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)\s+(\d{4})', parts, re.IGNORECASE)
                if date_match:
                    jour = date_match.group(1).zfill(2)
                    mois = mois_map.get(date_match.group(2).lower(), '01')
                    annee = date_match.group(3)
                    date_cours = f"{annee}-{mois}-{jour}"
                    # Calculer le jour de la semaine
                    try:
                        date_obj = datetime.strptime(date_cours, '%Y-%m-%d')
                        jour_semaine = jours_semaine[date_obj.weekday()]
                    except:
                        pass
                    # Retirer la date du texte
                    parts = parts[:date_match.start()].strip() + ' ' + parts[date_match.end():].strip()
                    parts = parts.strip()

                # Format: le 29/12/2025 ou le 29-12-2025
                if not date_cours:
                    date_match2 = re.search(r'le\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', parts, re.IGNORECASE)
                    if date_match2:
                        jour = date_match2.group(1).zfill(2)
                        mois = date_match2.group(2).zfill(2)
                        annee = date_match2.group(3)
                        date_cours = f"{annee}-{mois}-{jour}"
                        try:
                            date_obj = datetime.strptime(date_cours, '%Y-%m-%d')
                            jour_semaine = jours_semaine[date_obj.weekday()]
                        except:
                            pass
                        parts = parts[:date_match2.start()].strip() + ' ' + parts[date_match2.end():].strip()
                        parts = parts.strip()

                # Si pas de date spécifiée, utiliser aujourd'hui
                if not date_cours:
                    date_cours = date.today().strftime('%Y-%m-%d')
                    jour_semaine = jours_semaine[date.today().weekday()]

                # Extraire les heures (format: de 09h à 11h, de 9h à 11h, de 09:00 à 11:00)
                heure_debut = None
                heure_fin = None
                heure_match = re.search(r'de\s+(\d{1,2})[h:]?(\d{0,2})?\s*[àa]\s*(\d{1,2})[h:]?(\d{0,2})?', parts, re.IGNORECASE)
                if heure_match:
                    h1 = heure_match.group(1).zfill(2)
                    m1 = heure_match.group(2).zfill(2) if heure_match.group(2) else '00'
                    h2 = heure_match.group(3).zfill(2)
                    m2 = heure_match.group(4).zfill(2) if heure_match.group(4) else '00'
                    heure_debut = f"{h1}:{m1}"
                    heure_fin = f"{h2}:{m2}"
                    # Retirer les heures du texte
                    parts = parts[:heure_match.start()].strip() + ' ' + parts[heure_match.end():].strip()
                    parts = parts.strip()

                # Extraire la salle (après "Salle" ou "salle")
                salle_match = re.search(r'\bsalle\s+(\S+)', parts, re.IGNORECASE)
                salle = salle_match.group(1).strip() if salle_match else 'Salle N/A'

                # Retirer la partie salle pour parser le reste
                if salle_match:
                    parts = parts[:salle_match.start()].strip()

                # Extraire la filière et le niveau
                filiere, niveau = extract_filiere_niveau(parts.lower())

                # Chercher le professeur par nom dans la base
                cursor.execute("SELECT id, prenom, nom FROM users WHERE role='professeur' AND school_id = %s", (tenant.current_school_id(),))
                professeurs = cursor.fetchall()

                professeur_id = None
                professeur_nom = None
                parts_lower = parts.lower()

                for prof in professeurs:
                    prof_nom_lower = prof['nom'].lower()
                    prof_prenom_lower = prof['prenom'].lower()
                    if prof_nom_lower in parts_lower or prof_prenom_lower in parts_lower:
                        professeur_id = prof['id']
                        professeur_nom = f"{prof['prenom']} {prof['nom']}"
                        # Retirer le nom du prof pour trouver le nom du cours
                        parts = re.sub(re.escape(prof['nom']), '', parts, flags=re.IGNORECASE)
                        parts = re.sub(re.escape(prof['prenom']), '', parts, flags=re.IGNORECASE)
                        break

                # Retirer filière et niveau du texte pour extraire le nom du cours
                if filiere:
                    parts = re.sub(re.escape(filiere), '', parts, flags=re.IGNORECASE)
                if niveau:
                    parts = re.sub(r'\b' + re.escape(niveau) + r'\b', '', parts, flags=re.IGNORECASE)
                    # Aussi retirer les formes courtes comme "M2", "L3", etc.
                    parts = re.sub(r'\b[MLml][1-3]\b', '', parts)
                    parts = re.sub(r'\bmaster\s*[12]\b', '', parts, flags=re.IGNORECASE)
                    parts = re.sub(r'\blicence\s*[123]\b', '', parts, flags=re.IGNORECASE)

                # Le reste est le nom du cours
                nom_cours = ' '.join(parts.split()).strip().title()

                if not nom_cours:
                    conn.close()
                    return {
                        "type": "warning",
                        "message": "⚠️ **Nom du cours non détecté.**\n\n"
                                  "**Format:**\n"
                                  "`Cours : [Nom] [Filière] [Niveau] [Professeur] Salle [Salle]`\n\n"
                                  "**Exemple:**\n"
                                  "`Cours : Fintech IA Master 2 RIAD Salle E2`"
                    }

                if not filiere:
                    conn.close()
                    return {
                        "type": "warning",
                        "message": f"⚠️ **Filière non détectée pour le cours '{nom_cours}'.**\n\n"
                                  "**Filières reconnues:** IA, CyberSécurité, Data Science, DevOps, etc.\n\n"
                                  "**Exemple:**\n"
                                  "`Cours : {nom_cours} IA Master 2 Salle E1`"
                    }

                # Créer le cours avec les heures et la date
                year_id = get_current_year_id()

                # Créer les datetime start/end avec la date spécifiée
                start_datetime = f"{date_cours} {heure_debut}:00" if heure_debut else None
                end_datetime = f"{date_cours} {heure_fin}:00" if heure_fin else None

                # Canoniser filière / niveau avant insertion
                filiere_row = resolve_filiere_by_name(cursor, filiere, tenant.current_school_id())
                filiere_canon = filiere_row['nom'] if filiere_row else filiere
                niveau_short = normaliser_niveau(niveau, fallback='') if niveau else ''
                niveau_canon = NIVEAU_SHORT_TO_LONG.get(niveau_short, niveau or '')

                cursor.execute('''
                    INSERT INTO courses (nom_cours, professeur_id, professeur_nom, filiere, niveau, salle,
                                        start, end, date_cours, jour_semaine, heure_debut, heure_fin, description, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (nom_cours, professeur_id, professeur_nom, filiere_canon, niveau_canon, salle,
                      start_datetime, end_datetime, date_cours, jour_semaine, heure_debut, heure_fin,
                      f"Cours de {nom_cours} créé via chatbot", year_id, tenant.current_school_id()))

                course_id = cursor.lastrowid

                # Ajouter à l'emploi du temps du professeur
                if professeur_id:
                    cursor.execute("""
                        INSERT IGNORE INTO emploi_temps (user_id, course_id, role, school_id)
                        VALUES (%s, %s, 'professeur', %s)
                    """, (professeur_id, course_id, tenant.current_school_id()))

                # Ajouter à l'emploi du temps des étudiants (matching tolérant)
                f_aliases = filiere_aliases(cursor, filiere_canon) or [filiere_canon]
                f_ph = ','.join(['%s'] * len(f_aliases))
                if niveau_canon:
                    n_aliases = niveau_aliases(niveau_canon) or [niveau_canon]
                    n_ph = ','.join(['%s'] * len(n_aliases))
                    cursor.execute(f"""
                        INSERT IGNORE INTO emploi_temps (user_id, course_id, role, school_id)
                        SELECT id, %s, 'etudiant', %s FROM users
                        WHERE role = 'etudiant' AND filiere IN ({f_ph}) AND niveau IN ({n_ph}) AND school_id = %s
                    """, (course_id, tenant.current_school_id(), *f_aliases, *n_aliases, tenant.current_school_id()))
                else:
                    cursor.execute(f"""
                        INSERT IGNORE INTO emploi_temps (user_id, course_id, role, school_id)
                        SELECT id, %s, 'etudiant', %s FROM users
                        WHERE role = 'etudiant' AND filiere IN ({f_ph}) AND school_id = %s
                    """, (course_id, tenant.current_school_id(), *f_aliases, tenant.current_school_id()))

                conn.commit()
                conn.close()

                prof_info = f"👨‍🏫 {professeur_nom}" if professeur_nom else "👨‍🏫 Non assigné"
                horaire_info = f"🕐 {heure_debut} - {heure_fin}" if heure_debut and heure_fin else ""
                date_info = f"📅 {jour_semaine} {date_cours}" if date_cours else ""

                return {
                    "type": "success",
                    "message": f"✅ **Cours créé avec succès !**\n\n"
                              f"📚 **{nom_cours}**\n"
                              f"📍 {filiere} - {niveau or 'Tous niveaux'}\n"
                              f"🏛️ Salle: {salle}\n"
                              f"{date_info}\n"
                              f"{horaire_info}\n"
                              f"{prof_info}\n\n"
                              f"💡 Les étudiants de {filiere} {niveau or ''} ont été automatiquement inscrits.",
                    "buttons": [
                        {"label": "📋 Voir les cours", "url": "/admin/courses"}
                    ]
                }

            except Exception as e:
                conn.close()
                import traceback
                print(f"Erreur création cours chatbot: {traceback.format_exc()}")
                return {
                    "type": "error",
                    "message": f"❌ Erreur lors de la création du cours: {str(e)}\n\n"
                              f"**Format:**\n"
                              f"`Cours : [Nom] [Filière] [Niveau] [Professeur] Salle [Salle]`"
                }

        # === PROGRAMMER UN COURS (mode interactif) ===
        elif contains_any(query_lower, ['programmer un nouveau cours', 'programmer un cours', 'programmer cours', 'planifier un cours', 'planifier cours',
                                        'ajouter un cours', 'nouveau cours', 'créer un cours', 'creer un cours',
                                        'ajouter seance', 'ajouter séance', 'nouvelle seance']):
            # Démarrer le mode interactif de création de cours
            set_course_creation_state({
                'active': True,
                'step': 'nom',
                'nom_cours': None,
                'filiere': None,
                'niveau': None,
                'professeur_id': None,
                'professeur_nom': None,
                'salle': None,
                'date_cours': None,
                'jour_semaine': None,
                'heure_debut': None,
                'heure_fin': None
            })
            conn.close()

            return {
                "type": "question",
                "message": "📚 **Assistant de création de cours**\n\n"
                          "Je vais vous guider étape par étape.\n"
                          "_Tapez 'annuler' à tout moment pour arrêter._\n\n"
                          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "📝 **Quel est le nom du cours ?**\n\n"
                          "_Exemple: Data Science, Machine Learning, Fintech..._"
            }

        # === CRÉER UN PROFESSEUR ===
        elif contains_any(query_lower, ['creer prof', 'créer prof', 'ajouter prof', 'nouveau prof', 'add prof', 'embaucher']):
            conn.close()
            return {
                "type": "action",
                "action": "create_professor",
                "message": "👨‍🏫 **Création d'un compte professeur**\n\n"
                          "Pour créer un professeur, utilisez le formulaire dédié:",
                "buttons": [
                    {"label": "➕ Ajouter un professeur", "url": "/admin/add_professeur"}
                ]
            }

        # === AJOUTER UNE DÉPENSE ===
        elif contains_any(query_lower, ['ajouter depense', 'ajouter dépense', 'nouvelle depense', 'nouvelle dépense', 'enregistrer depense']):
            conn.close()
            return {
                "type": "action",
                "action": "create_expense",
                "message": "💸 **Enregistrer une dépense**\n\n"
                          "Accédez au formulaire de gestion des dépenses:",
                "buttons": [
                    {"label": "💸 Gérer les dépenses", "url": "/admin/depenses"}
                ]
            }

        # === RECHERCHER (générique) ===
        elif contains_any(query_lower, ['chercher', 'trouver', 'rechercher', 'search']):
            # Extraire le terme de recherche
            search_term = re.sub(r'(chercher|trouver|rechercher|search|l\'|le|la|les|un|une|des)', '', query_lower).strip()

            if search_term:
                # Rechercher dans les étudiants
                cursor.execute("""
                    SELECT id, prenom, nom, email, filiere, niveau, role
                    FROM users
                    WHERE (LOWER(nom) LIKE %s OR LOWER(prenom) LIKE %s OR LOWER(email) LIKE %s)
                    AND school_id = %s
                    ORDER BY role, nom
                    LIMIT 15
                """, (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', tenant.current_school_id()))
                resultats = cursor.fetchall()
                conn.close()

                if not resultats:
                    return {"type": "info", "message": f"📭 Aucun résultat trouvé pour '{search_term}'"}

                msg = f"🔍 **Résultats pour '{search_term}'** ({len(resultats)} trouvés)\n\n"
                for r in resultats:
                    role_icon = "👨‍🎓" if r['role'] == 'etudiant' else ("👨‍🏫" if r['role'] == 'professeur' else "👤")
                    msg += f"{role_icon} **{r['prenom']} {r['nom']}** ({r['role']})\n   📧 {r['email']}\n\n"

                return {"type": "search", "message": msg, "data": resultats}
            else:
                conn.close()
                return {"type": "info", "message": "💡 Précisez ce que vous recherchez.\nExemple: `Chercher Dupont`"}

        # === PRÉSENCES / ABSENCES ===
        elif contains_any(query_lower, ['presence', 'présence', 'absence', 'assiduite', 'assiduité', 'present', 'présent', 'absent']):
            conn.close()
            return {
                "type": "navigation",
                "message": "📋 **Gestion des présences**\n\nAccédez au module de suivi des présences:",
                "buttons": [
                    {"label": "📋 Gestion des présences", "url": "/admin/absences"}
                ]
            }

        # === CLASSES ===
        elif contains_any(query_lower, ['classe', 'groupe', 'section', 'promo', 'promotion']):
            conn.close()
            return {
                "type": "navigation",
                "message": "🏫 **Gestion des classes**\n\nAccédez au module de gestion des classes:",
                "buttons": [
                    {"label": "🏫 Gestion des classes", "url": "/admin/classes"}
                ]
            }

        # === NAVIGUER VERS LES PAGES ===
        elif contains_any(query_lower, ['aller', 'acceder', 'accéder', 'ouvrir', 'voir page', 'naviguer']):
            conn.close()
            buttons = []
            message = "🔗 **Navigation rapide**\n\nVoici les liens vers les différentes sections:\n"

            if 'cours' in query_lower:
                buttons.append({"label": "📚 Gestion des cours", "url": "/admin/dashboard"})
            elif 'finance' in query_lower or 'paiement' in query_lower:
                buttons.append({"label": "💰 Gestion des finances", "url": "/admin/finance"})
            elif 'note' in query_lower or 'grade' in query_lower:
                buttons.append({"label": "⭐ Gestion des notes", "url": "/admin/grades"})
            elif 'présence' in query_lower or 'absence' in query_lower:
                buttons.append({"label": "📋 Gestion des présences", "url": "/admin/absences"})
            elif 'classe' in query_lower:
                buttons.append({"label": "🏫 Gestion des classes", "url": "/admin/classes"})
            elif 'stat' in query_lower:
                buttons.append({"label": "📊 Statistiques", "url": "/admin/stats"})
            else:
                buttons = [
                    {"label": "📚 Cours", "url": "/admin/dashboard"},
                    {"label": "💰 Finances", "url": "/admin/finance"},
                    {"label": "⭐ Notes", "url": "/admin/grades"},
                    {"label": "📋 Présences", "url": "/admin/absences"},
                    {"label": "🏫 Classes", "url": "/admin/classes"},
                    {"label": "📊 Statistiques", "url": "/admin/stats"}
                ]

            return {"type": "navigation", "message": message, "buttons": buttons}

        # === AIDE / COMMANDES DISPONIBLES ===
        elif contains_any(query_lower, ['aide', 'help', 'commande', 'quoi faire', 'que peux', 'comment', 'bonjour', 'salut', 'hello']):
            conn.close()
            return {
                "type": "help",
                "message": """🤖 **Assistant Administrateur AdsClass**

📊 **Statistiques**
• `Statistiques` - Vue d'ensemble

👨‍🎓 **Étudiants / Professeurs**
• `Étudiants IA Master 2`
• `Professeurs`

📚 **Programmer un cours**
• `Cours : [Nom] [Filière] [Niveau] Salle [Salle] de [Heure] à [Heure]`

💰 **Paiements**
• `Ajouter un paiement` - Ouvrir le formulaire
• `Paiements de [nom étudiant]` - Historique
• `Modifier paiement #123` - Modifier un paiement
• `Imprimer reçu #123` - Générer le reçu
• `Paiements du jour` - Liste du jour

💵 **Finances**
• `Recettes` / `Dépenses` / `Bilan`

🔗 **Navigation**
• `Cours` / `Notes` / `Présences`

💡 **Filières:** IA, Big Data, CCA, Marketing
💡 **Niveaux:** L1, L2, L3, M1, M2"""
            }

        # === COMPTAGE ===
        elif contains_any(query_lower, ['combien', 'nombre', 'total', 'count']):
            if contains_any(query_lower, ['etudiant', 'étudiant', 'eleve', 'élève', 'inscrit']):
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
                count = cursor.fetchone()['count']
                conn.close()
                return {"type": "info", "message": f"👨‍🎓 Il y a **{count} étudiants** inscrits dans AdsClass."}
            elif contains_any(query_lower, ['prof', 'professeur', 'enseignant']):
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='professeur' AND school_id = %s", (tenant.current_school_id(),))
                count = cursor.fetchone()['count']
                conn.close()
                return {"type": "info", "message": f"👨‍🏫 Il y a **{count} professeurs** dans AdsClass."}
            elif contains_any(query_lower, ['cours', 'module', 'matiere']):
                cursor.execute("SELECT COUNT(*) as count FROM courses WHERE school_id = %s", (tenant.current_school_id(),))
                count = cursor.fetchone()['count']
                conn.close()
                return {"type": "info", "message": f"📚 Il y a **{count} cours** programmés."}
            else:
                conn.close()
                return {"type": "info", "message": "💡 Précisez ce que vous voulez compter: étudiants, professeurs, ou cours."}

        # === RÉPONSE PAR DÉFAUT ===
        else:
            conn.close()
            return {
                "type": "default",
                "message": f"🤔 Je n'ai pas compris votre demande: \"{query}\"\n\n"
                          "💡 **Essayez par exemple:**\n"
                          "• `Afficher les statistiques`\n"
                          "• `Liste des étudiants`\n"
                          "• `Créer un cours`\n"
                          "• `Aide` pour voir toutes les commandes"
            }

    except Exception as e:
        if conn:
            conn.close()
        return {"type": "error", "message": f"❌ Erreur: {str(e)}"}


def admin_chatbot_ask():
    """API pour le chatbot administrateur"""
    try:
        data = request.get_json()
        query = data.get('question', '').strip()

        if not query:
            return jsonify({'error': 'Question vide'}), 400

        if len(query) < 2:
            return jsonify({'error': 'Question trop courte'}), 400

        user_id = session['user_id']
        result = admin_chatbot_handle(query, user_id)

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def admin_chatbot_status():
    """Statut Groq pour le chatbot administrateur"""
    groq_configured = bool(AI_CONFIG.get('groq_api_key'))
    ai_ok = False
    model = AI_CONFIG.get('groq_model', '')

    if groq_configured and AI_CONFIG['provider'] == 'groq':
        try:
            test_resp = _ai_http_post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_CONFIG['groq_api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ok"}],
                    "max_tokens": 5
                },
                timeout=15
            )
            ai_ok = test_resp.status_code == 200
        except Exception as e:
            print(f"Test Groq admin échoué: {e}")

    return jsonify({
        'ai_available': ai_ok,
        'provider': AI_CONFIG['provider'],
        'model': model,
        'groq_configured': groq_configured
    })


def admin_chatbot_suggestions():
    """Suggestions de commandes pour le chatbot admin"""
    suggestions = [
        "Afficher les statistiques de l'école",
        "Liste des étudiants IA Master 2",
        "Ajouter un paiement étudiant",
        "Programmer un nouveau cours",
        "Statut paiement de Diallo",
        "Paiements du jour",
        "Bilan financier",
        "Liste des professeurs"
    ]
    return jsonify({'suggestions': suggestions})


def admin_chatbot_page():
    """Page du chatbot administrateur style ChatGPT"""
    return render_template('admin_chatbot.html')


def chatbot_search_students():
    """Recherche d'étudiants pour le chatbot"""
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify({'students': []})

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion'}), 500

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, prenom, nom, email, filiere, niveau
        FROM users
        WHERE role = 'etudiant'
        AND (LOWER(nom) LIKE %s OR LOWER(prenom) LIKE %s OR LOWER(email) LIKE %s)
        AND school_id = %s
        ORDER BY nom, prenom
        LIMIT 10
    """, (f'%{query.lower()}%', f'%{query.lower()}%', f'%{query.lower()}%', tenant.current_school_id()))
    students = cursor.fetchall()
    conn.close()

    return jsonify({'students': students})


def chatbot_add_payment():
    """Ajouter un paiement via le chatbot"""
    data = request.get_json()

    etudiant_id = data.get('etudiant_id')
    date = data.get('date')
    montant = data.get('montant')
    moyen = data.get('moyen', 'Espèces')
    observation = data.get('observation', '')

    if not etudiant_id or not date or not montant:
        return jsonify({'error': 'Données incomplètes'}), 400

    try:
        montant = float(montant)
    except ValueError:
        return jsonify({'error': 'Montant invalide'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion'}), 500

    cursor = conn.cursor(dictionary=True)

    # Vérifier que l'étudiant existe
    cursor.execute("SELECT id, prenom, nom FROM users WHERE id = %s AND role = 'etudiant' AND school_id = %s", (etudiant_id, tenant.current_school_id()))
    etudiant = cursor.fetchone()
    if not etudiant:
        conn.close()
        return jsonify({'error': 'Étudiant non trouvé'}), 404

    year_id = get_current_year_id()
    cursor.execute(
        "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation, annee_academique_id, school_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (etudiant_id, date, montant, moyen, observation, year_id, tenant.current_school_id())
    )
    conn.commit()
    paiement_id = cursor.lastrowid
    conn.close()

    return jsonify({
        'success': True,
        'message': f'Paiement de {montant:,.0f} FCFA ajouté pour {etudiant["prenom"]} {etudiant["nom"]}',
        'paiement_id': paiement_id
    })


def chatbot_search_payments():
    """Recherche de paiements via le chatbot"""
    etudiant_id = request.args.get('etudiant_id')
    date = request.args.get('date')

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion'}), 500

    cursor = conn.cursor(dictionary=True)

    sql = """
        SELECT p.id, p.date, p.montant, p.moyen, p.observation, p.etudiant_id,
               u.prenom, u.nom
        FROM paiements p
        JOIN users u ON p.etudiant_id = u.id
        WHERE 1=1
    """
    params = []

    if etudiant_id:
        sql += " AND p.etudiant_id = %s"
        params.append(etudiant_id)

    if date:
        sql += " AND p.date = %s"
        params.append(date)

    sql += " ORDER BY p.date DESC LIMIT 20"

    cursor.execute(sql, params)
    paiements = cursor.fetchall()
    conn.close()

    # Convertir les dates
    for p in paiements:
        if p.get('date'):
            p['date'] = str(p['date'])

    return jsonify({'paiements': paiements})


def register_chatbot_admin_routes(app, deps):
    """Enregistrer les routes du chatbot administrateur sur l'application Flask."""
    global get_db_connection, get_current_year_id, login_required, admin_required
    get_db_connection = deps['get_db_connection']
    get_current_year_id = deps['get_current_year_id']
    login_required = deps['login_required']
    admin_required = deps['admin_required']

    def guard(fn):
        return login_required(admin_required(fn))

    app.add_url_rule('/api/admin/chatbot/ask', 'admin_chatbot_ask',
                     guard(admin_chatbot_ask), methods=['POST'])
    app.add_url_rule('/api/admin/chatbot/status', 'admin_chatbot_status',
                     guard(admin_chatbot_status), methods=['GET'])
    app.add_url_rule('/api/admin/chatbot/suggestions', 'admin_chatbot_suggestions',
                     guard(admin_chatbot_suggestions), methods=['GET'])
    app.add_url_rule('/admin/chatbot', 'admin_chatbot_page',
                     guard(admin_chatbot_page))
    app.add_url_rule('/api/admin/chatbot/search-students', 'chatbot_search_students',
                     guard(chatbot_search_students), methods=['GET'])
    app.add_url_rule('/api/admin/chatbot/add-payment', 'chatbot_add_payment',
                     guard(chatbot_add_payment), methods=['POST'])
    app.add_url_rule('/api/admin/chatbot/search-payments', 'chatbot_search_payments',
                     guard(chatbot_search_payments), methods=['GET'])
