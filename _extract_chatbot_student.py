# One-off extraction script for Wave 8b (student chatbot).
# Copies exact source bytes from app.py into routes/chatbot_student.py.
# Run once, verify, then delete this file.

with open('app.py', encoding='utf-8') as f:
    lines = f.readlines()  # lines[i] == source line i+1


def slice_lines(a, b):
    """Inclusive 1-based slice a..b."""
    return lines[a - 1:b]


def strip_decorators(chunk):
    out = []
    for ln in chunk:
        s = ln.lstrip()
        if s.startswith('@app.route') or s.startswith('@login_required'):
            continue
        if 'fonctions IA' in ln and 'routes/chatbot_ai' in ln:
            continue
        out.append(ln)
    return out


# Globals + helpers + 4 API routes (contiguous)
block_a = strip_decorators(slice_lines(6799, 7920))
# /student/chatbot page route (separate location)
block_p = strip_decorators(slice_lines(4553, 4561))

HEADER = '''"""
Module Chatbot Etudiant - AdsClass
Helpers PDF/knowledge-base + routes API etudiant + page /student/chatbot.
Extrait de app.py sans aucune modification de logique.
"""

import os
from datetime import datetime
from flask import (
    session, request, jsonify, render_template,
    flash, redirect, url_for,
)
import tenant
from routes.chatbot_ai import (
    AI_CONFIG,
    _ai_http_post,
    check_ai_available,
    generate_ai_response,
)

# Dependance injectee par register_chatbot_student_routes
get_db_connection = None

'''

FOOTER = '''

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
'''

with open('routes/chatbot_student.py', 'w', encoding='utf-8') as f:
    f.write(HEADER)
    f.writelines(block_a)
    f.write('\n\n')
    f.writelines(block_p)
    f.write(FOOTER)

print('chatbot_student.py written')
