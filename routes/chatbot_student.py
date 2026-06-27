"""
Module Chatbot Etudiant - AdsClass
Assistant pedagogique base EXCLUSIVEMENT sur les PDF uploades par les
professeurs et enregistres en base (table documents). Reponses generees
via Groq (Llama) a partir du contexte documentaire.

Isolation stricte multi-tenant : chaque requete est filtree par school_id,
par l'inscription de l'etudiant (emploi_temps) et donc par filiere/niveau.
Un etudiant de l'Ecole A ne peut jamais interroger un PDF de l'Ecole B.
"""

import os
import re

from flask import (
    session, request, jsonify, render_template,
    flash, redirect, url_for,
)

import tenant
from student_enrollment_service import student_enrollment_join_sql
from routes.chatbot_ai import (
    AI_CONFIG,
    check_ai_available,
    generate_ai_response,
)

# Dependance injectee par register_chatbot_student_routes
get_db_connection = None

# Cache memoire du texte PDF extrait, isole par school_id (pas de contamination
# d'index entre ecoles). Structure : { cache_key: { doc_id: texte } }
_knowledge_cache = {}

STOPWORDS = set((
    "le la les un une des de du et a au aux en pour par sur dans que qui quoi "
    "est sont ce cette ces mon ma mes ton ta il elle on nous vous ils elles ne "
    "pas plus avec sans comment pourquoi quel quelle quels quelles mais ou donc"
).split())


def _knowledge_cache_key(school_id):
    """Clef de cache strictement isolee par ecole."""
    return f"kb::school::{int(school_id)}"


def _resolve_pdf_path(chemin_fichier, nom_fichier=None):
    """Resout le chemin disque d'un PDF de maniere robuste (uploads/)."""
    candidates = []
    if chemin_fichier:
        candidates.append(chemin_fichier)
        candidates.append(os.path.join('uploads', chemin_fichier))
        candidates.append(os.path.join(os.getcwd(), 'uploads', chemin_fichier))
        candidates.append(os.path.join('uploads', os.path.basename(chemin_fichier)))
    if nom_fichier:
        candidates.append(os.path.join('uploads', nom_fichier))
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _extract_pdf_text(path, max_chars=20000):
    """Extrait le texte d'un PDF avec PyMuPDF (fitz)."""
    try:
        import fitz
    except Exception:
        return ""
    try:
        parts = []
        with fitz.open(path) as doc:
            for page in doc:
                parts.append(page.get_text())
                if sum(len(t) for t in parts) >= max_chars:
                    break
        return "\n".join(parts)[:max_chars]
    except Exception as e:
        print(f"Erreur extraction PDF {path}: {e}")
        return ""


def get_db_pdf_documents(user_id):
    """
    Source de verite unique : les PDF visibles des cours auxquels l'etudiant
    est inscrit, dans SON ecole. Double filtrage school_id (document + cours)
    + inscription tenant-safe (emploi_temps).
    """
    conn = get_db_connection()
    if not conn:
        return []
    try:
        sid = tenant.current_school_id()
        cursor = conn.cursor(dictionary=True)
        et_join = student_enrollment_join_sql('c', 'et')
        cursor.execute(f"""
            SELECT DISTINCT d.id, d.titre, d.nom_fichier, d.chemin_fichier,
                   d.course_id, c.nom_cours, c.filiere, c.niveau
            FROM documents d
            JOIN courses c ON d.course_id = c.id AND d.school_id = c.school_id
            {et_join}
            WHERE et.user_id = %s AND et.role = 'etudiant'
              AND d.visible = 1
              AND d.school_id = %s AND c.school_id = %s
              AND (LOWER(d.nom_fichier) LIKE %s OR LOWER(d.chemin_fichier) LIKE %s)
            ORDER BY d.id DESC
        """, (user_id, sid, sid, '%.pdf', '%.pdf'))
        return cursor.fetchall()
    except Exception as e:
        print(f"Erreur get_db_pdf_documents: {e}")
        return []
    finally:
        conn.close()


def _build_knowledge(user_id):
    """Construit la base de connaissances (texte) des PDF accessibles."""
    sid = tenant.current_school_id()
    bucket = _knowledge_cache.setdefault(_knowledge_cache_key(sid), {})
    knowledge = []
    for d in get_db_pdf_documents(user_id):
        doc_id = d['id']
        text = bucket.get(doc_id)
        if text is None:
            path = _resolve_pdf_path(d.get('chemin_fichier'), d.get('nom_fichier'))
            text = _extract_pdf_text(path) if path else ""
            bucket[doc_id] = text
        if text:
            knowledge.append({
                'id': doc_id,
                'titre': d.get('titre') or d.get('nom_fichier') or 'Document',
                'cours': d.get('nom_cours') or '',
                'text': text,
            })
    return knowledge


def _tokenize(text):
    words = re.findall(r"[a-zA-Z\u00c0-\u017f0-9]+", (text or '').lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def _select_context(query, knowledge, max_chars=8000):
    """Selectionne les documents les plus pertinents et construit le contexte."""
    q_tokens = set(_tokenize(query))
    scored = []
    for k in knowledge:
        text_lower = k['text'].lower()
        matches = sum(1 for t in q_tokens if t in text_lower)
        scored.append((matches, k))
    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [k for (m, k) in scored if m > 0][:3]
    fallback = False
    if not selected:
        fallback = True
        selected = [item[1] for item in scored][:2]

    parts, used, total = [], [], 0
    for k in selected:
        budget = max_chars - total
        if budget <= 0:
            break
        chunk = (f"### Document: {k['titre']} (Cours: {k['cours']})\n{k['text']}")[:budget]
        parts.append(chunk)
        total += len(chunk)
        used.append(k)

    if q_tokens:
        matched = sum(1 for t in q_tokens if any(t in k['text'].lower() for k in used))
        conf = int(round(100 * matched / len(q_tokens)))
    else:
        conf = 0
    if fallback:
        conf = min(conf, 35)
    conf = max(5, min(95, conf))
    return "\n\n".join(parts), used, conf


def _fallback_answer(context):
    """Reponse de secours quand l'IA est indisponible (extrait documentaire)."""
    snippet = (context or '').strip()
    if not snippet:
        return ("## Information indisponible\n\nJe n'ai pas trouve d'element pertinent "
                "dans vos documents de cours pour repondre a cette question. "
                "Essayez de reformuler votre demande.")
    snippet = snippet[:1200]
    return ("## Elements trouves dans vos documents\n\n"
            "Voici les passages les plus pertinents extraits de vos supports de cours :\n\n"
            f"> {snippet}\n\n"
            "### En resume\nReformulez votre question pour une reponse plus ciblee, "
            "ou consultez le document source indique ci-dessous.")


# ============================================================
# ROUTES
# ============================================================

def student_chatbot():
    """Page de l'assistant IA etudiant."""
    if session.get('role') != 'etudiant':
        flash("Acces refuse.", "danger")
        return redirect(url_for('login'))
    return render_template('student_chatbot.html')


def chatbot_ask():
    """API : repond a une question a partir des PDF de l'etudiant (Groq/Llama)."""
    if session.get('role') != 'etudiant':
        return jsonify({'error': "Acces refuse."}), 403

    data = request.get_json(silent=True) or {}
    question = (data.get('question') or data.get('query') or '').strip()
    if not question:
        return jsonify({'error': "Veuillez saisir une question."}), 400

    knowledge = _build_knowledge(session['user_id'])
    if not knowledge:
        return jsonify({
            'response': ("## Aucun document disponible\n\nAucun support de cours (PDF) "
                         "n'est encore disponible pour vos modules. Revenez une fois que "
                         "vos enseignants auront publie des documents."),
            'sources': [],
            'ai_powered': False,
            'confidence': 0,
        })

    context, used, confidence = _select_context(question, knowledge)
    doc_info = {
        'cours': used[0]['cours'] if used else '',
        'titre': used[0]['titre'] if used else '',
    }

    answer, ai_powered = None, False
    if check_ai_available() and context.strip():
        answer = generate_ai_response(question, context, doc_info, None)
    if answer:
        ai_powered = True
    else:
        answer = _fallback_answer(context)

    sources = [{'titre': k['titre'], 'document': k['titre'], 'cours': k['cours']} for k in used]
    return jsonify({
        'response': answer,
        'sources': sources,
        'ai_powered': ai_powered,
        'confidence': confidence if ai_powered else min(confidence, 40),
    })


def chatbot_status():
    """API : etat du service IA + nombre de documents accessibles."""
    if session.get('role') != 'etudiant':
        return jsonify({'available': False}), 403
    docs = get_db_pdf_documents(session['user_id'])
    provider = AI_CONFIG.get('provider')
    model = AI_CONFIG.get('groq_model') if provider == 'groq' else AI_CONFIG.get('ollama_model')
    return jsonify({
        'available': check_ai_available(),
        'provider': provider,
        'model': model,
        'documents_count': len(docs),
    })


def chatbot_documents():
    """API : liste des documents (PDF) accessibles a l'etudiant."""
    if session.get('role') != 'etudiant':
        return jsonify({'count': 0, 'documents': []}), 403
    docs = get_db_pdf_documents(session['user_id'])
    return jsonify({
        'count': len(docs),
        'documents': [
            {'titre': d.get('titre') or d.get('nom_fichier'), 'cours': d.get('nom_cours')}
            for d in docs
        ],
    })


def chatbot_suggestions():
    """API : suggestions de questions basees sur les cours de l'etudiant."""
    if session.get('role') != 'etudiant':
        return jsonify({'suggestions': []}), 403
    cours = []
    for d in get_db_pdf_documents(session['user_id']):
        c = d.get('nom_cours')
        if c and c not in cours:
            cours.append(c)
    suggestions = [f"Resume le cours de {c}" for c in cours[:4]]
    if not suggestions:
        suggestions = ["Explique-moi un concept cle", "Donne-moi un exemple concret"]
    return jsonify({'suggestions': suggestions})


def register_chatbot_student_routes(app, deps):
    """Enregistrer les routes du chatbot etudiant sur l'application Flask."""
    global get_db_connection
    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']

    app.add_url_rule('/student/chatbot', 'student_chatbot',
                     login_required(student_chatbot))
    app.add_url_rule('/api/chatbot/ask', 'chatbot_ask',
                     login_required(chatbot_ask), methods=['POST'])
    app.add_url_rule('/api/chatbot/status', 'chatbot_status',
                     login_required(chatbot_status), methods=['GET'])
    app.add_url_rule('/api/chatbot/documents', 'chatbot_documents',
                     login_required(chatbot_documents), methods=['GET'])
    app.add_url_rule('/api/chatbot/suggestions', 'chatbot_suggestions',
                     login_required(chatbot_suggestions), methods=['GET'])
