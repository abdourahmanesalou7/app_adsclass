"""
Module Admissions CRM — AdsClass
Pipeline candidats, Kanban, IA, communications, paiements, conversion étudiant.
"""

import os
import re
import json
import random
import requests
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

from services.admissions_services import (
    WhatsAppService, PaymentService, INTEGRATIONS,
    is_whatsapp_configured, is_stripe_configured, is_flutterwave_configured,
    WHATSAPP_TEMPLATES,
)
from student_enrollment_service import enrollir_candidat_en_etudiant
import tenant

# === CONSTANTES ===

ADMISSIONS_STAGES = [
    ('prospect', 'Prospects'),
    ('demande_info', 'Demande d\'information'),
    ('candidature', 'Candidature'),
    ('documents_recus', 'Documents reçus'),
    ('evaluation', 'Évaluation'),
    ('entretien', 'Entretien'),
    ('admis', 'Admis'),
    ('paiement', 'Paiement'),
    ('inscrit', 'Inscrit'),
]

STAGE_CODES = [s[0] for s in ADMISSIONS_STAGES]
STAGE_LABELS = {s[0]: s[1] for s in ADMISSIONS_STAGES}

DOCUMENT_TYPES = [
    ('bac', 'Diplôme du Bac'),
    ('releves', 'Relevés de notes'),
    ('cv', 'CV'),
    ('lettre_motivation', 'Lettre de motivation'),
    ('identite', 'Passeport / CIN'),
]

PAYMENT_METHODS = [
    ('stripe', 'Stripe'),
    ('nita', 'Nita Transfert'),
    ('amana', 'Amana Transfert'),
    ('orange_money', 'Orange Money'),
    ('airtel_money', 'Airtel Money'),
    ('zamani', 'Zamani'),
    ('especes', 'Espèces'),
    ('virement', 'Virement bancaire'),
]

LEAD_SOURCES = [
    ('site_web', 'Site web'),
    ('whatsapp', 'WhatsApp'),
    ('facebook', 'Facebook / Réseaux'),
    ('salon', 'Salon / Événement'),
    ('referral', 'Recommandation'),
    ('autre', 'Autre'),
]

UPLOAD_FOLDER = os.path.join('static', 'uploads', 'admissions')
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

AI_CONFIG = {
    'provider': os.environ.get('AI_PROVIDER', 'groq'),
    'groq_api_key': os.environ.get('GROQ_API_KEY', ''),
    'groq_model': os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile'),
    'openai_api_key': os.environ.get('OPENAI_API_KEY', ''),
    'openai_model': os.environ.get('OPENAI_MODEL', 'gpt-3.5-turbo'),
    'max_tokens': int(os.environ.get('AI_MAX_TOKENS', '1024')),
    'temperature': float(os.environ.get('AI_TEMPERATURE', '0.4')),
}


def init_admissions_tables(get_db_connection):
    """Créer les tables du module Admissions CRM."""
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_candidats (
                id INT PRIMARY KEY AUTO_INCREMENT,
                reference VARCHAR(20) UNIQUE,
                nom VARCHAR(100) NOT NULL,
                prenom VARCHAR(100) NOT NULL,
                email VARCHAR(150) NOT NULL,
                telephone VARCHAR(30),
                pays VARCHAR(80) DEFAULT 'Niger',
                programme_souhaite VARCHAR(150),
                filiere_id INT NULL,
                niveau VARCHAR(20) DEFAULT 'L1',
                source_lead VARCHAR(50) DEFAULT 'site_web',
                statut ENUM(
                    'prospect', 'demande_info', 'candidature', 'documents_recus',
                    'evaluation', 'entretien', 'admis', 'paiement', 'inscrit'
                ) DEFAULT 'prospect',
                notes TEXT,
                score_ia INT NULL,
                points_forts_ia TEXT,
                points_faibles_ia TEXT,
                probabilite_inscription INT NULL,
                etudiant_id INT NULL,
                annee_academique_id INT NULL,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                cree_par INT NULL,
                INDEX idx_statut (statut),
                INDEX idx_email (email),
                INDEX idx_annee (annee_academique_id),
                FOREIGN KEY (etudiant_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_documents (
                id INT PRIMARY KEY AUTO_INCREMENT,
                candidat_id INT NOT NULL,
                type_document VARCHAR(50) NOT NULL,
                nom_fichier VARCHAR(255) NOT NULL,
                chemin_fichier VARCHAR(500) NOT NULL,
                taille_octets INT DEFAULT 0,
                date_upload DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidat_id) REFERENCES admissions_candidats(id) ON DELETE CASCADE,
                INDEX idx_candidat (candidat_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_communications (
                id INT PRIMARY KEY AUTO_INCREMENT,
                candidat_id INT NOT NULL,
                canal ENUM('whatsapp', 'email', 'sms') NOT NULL,
                sujet VARCHAR(200),
                message TEXT NOT NULL,
                envoye_par INT NULL,
                statut ENUM('envoye', 'planifie', 'echec') DEFAULT 'envoye',
                date_envoi DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidat_id) REFERENCES admissions_candidats(id) ON DELETE CASCADE,
                INDEX idx_candidat (candidat_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_entretiens (
                id INT PRIMARY KEY AUTO_INCREMENT,
                candidat_id INT NOT NULL,
                date_entretien DATETIME NOT NULL,
                duree_minutes INT DEFAULT 30,
                type_entretien VARCHAR(50) DEFAULT 'presentiel',
                lieu VARCHAR(200),
                lien_visio VARCHAR(500),
                statut ENUM('planifie', 'confirme', 'termine', 'annule', 'absent') DEFAULT 'planifie',
                notes TEXT,
                intervieweur_id INT NULL,
                invitation_envoyee BOOLEAN DEFAULT FALSE,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidat_id) REFERENCES admissions_candidats(id) ON DELETE CASCADE,
                INDEX idx_date (date_entretien),
                INDEX idx_candidat (candidat_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_paiements (
                id INT PRIMARY KEY AUTO_INCREMENT,
                candidat_id INT NOT NULL,
                montant DOUBLE NOT NULL,
                devise VARCHAR(10) DEFAULT 'FCFA',
                moyen_paiement VARCHAR(50) NOT NULL,
                reference_transaction VARCHAR(100),
                statut ENUM('en_attente', 'confirme', 'echec', 'rembourse') DEFAULT 'en_attente',
                observation TEXT,
                date_paiement DATETIME DEFAULT CURRENT_TIMESTAMP,
                confirme_par INT NULL,
                FOREIGN KEY (candidat_id) REFERENCES admissions_candidats(id) ON DELETE CASCADE,
                INDEX idx_candidat (candidat_id),
                INDEX idx_statut (statut)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_historique (
                id INT PRIMARY KEY AUTO_INCREMENT,
                candidat_id INT NOT NULL,
                action VARCHAR(100) NOT NULL,
                ancien_statut VARCHAR(50),
                nouveau_statut VARCHAR(50),
                effectue_par INT NULL,
                commentaire TEXT,
                date_action DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidat_id) REFERENCES admissions_candidats(id) ON DELETE CASCADE,
                INDEX idx_candidat (candidat_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions_filiere_config (
                filiere_id INT PRIMARY KEY,
                frais_inscription DOUBLE DEFAULT 75000,
                places_disponibles INT DEFAULT 50,
                est_ouvert BOOLEAN DEFAULT TRUE,
                description_admission TEXT,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (filiere_id) REFERENCES filieres(id) ON DELETE CASCADE
            )
        """)

        # Colonnes additionnelles (migration safe)
        migrations = [
            "ALTER TABLE admissions_candidats ADD COLUMN payment_token VARCHAR(64) UNIQUE NULL",
            "ALTER TABLE admissions_paiements ADD COLUMN provider VARCHAR(30) NULL",
            "ALTER TABLE admissions_paiements ADD COLUMN stripe_session_id VARCHAR(100) NULL",
            "ALTER TABLE admissions_paiements ADD COLUMN flutterwave_tx_ref VARCHAR(100) NULL",
            "ALTER TABLE admissions_paiements ADD COLUMN payment_url VARCHAR(500) NULL",
            "ALTER TABLE admissions_paiements ADD COLUMN webhook_data TEXT NULL",
            "ALTER TABLE admissions_communications ADD COLUMN wa_message_id VARCHAR(100) NULL",
            "ALTER TABLE admissions_communications ADD COLUMN provider VARCHAR(30) DEFAULT 'manual'",
            "ALTER TABLE admissions_communications ADD COLUMN delivery_status VARCHAR(30) DEFAULT 'sent'",
        ]
        for sql in migrations:
            try:
                cursor.execute(sql)
            except Exception:
                pass

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Tables Admissions CRM initialisées")
    except Exception as e:
        print(f"❌ Erreur init admissions: {e}")


def register_admissions_routes(app, deps):
    """Enregistrer toutes les routes Admissions sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']
    get_current_year_id = deps['get_current_year_id']
    generate_password_hash = deps['generate_password_hash']

    def _allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    def _gen_reference():
        return f"ADM-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}"

    def _log_historique(cursor, candidat_id, action, ancien=None, nouveau=None, commentaire=None):
        cursor.execute("""
            INSERT INTO admissions_historique
            (candidat_id, action, ancien_statut, nouveau_statut, effectue_par, commentaire)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (candidat_id, action, ancien, nouveau, session.get('user_id'), commentaire))

    def _get_candidat(cursor, candidat_id):
        cursor.execute("""
            SELECT c.*, f.nom as filiere_nom
            FROM admissions_candidats c
            LEFT JOIN filieres f ON c.filiere_id = f.id
            WHERE c.id = %s AND c.school_id = %s
        """, (candidat_id, tenant.current_school_id()))
        return cursor.fetchone()

    def _get_stats(cursor, year_id=None):
        stats = {'prospects': 0, 'candidatures': 0, 'entretiens': 0, 'admis': 0, 'inscrits': 0, 'taux_conversion': 0}
        where = "WHERE annee_academique_id = %s AND school_id = %s" if year_id else "WHERE school_id = %s"
        params = (year_id, tenant.current_school_id()) if year_id else (tenant.current_school_id(),)

        cursor.execute(f"SELECT statut, COUNT(*) as nb FROM admissions_candidats {where} GROUP BY statut", params)
        by_stage = {row['statut']: row['nb'] for row in cursor.fetchall()}

        stats['prospects'] = by_stage.get('prospect', 0) + by_stage.get('demande_info', 0)
        stats['candidatures'] = sum(by_stage.get(s, 0) for s in ['candidature', 'documents_recus', 'evaluation'])
        stats['entretiens'] = by_stage.get('entretien', 0)
        stats['admis'] = by_stage.get('admis', 0) + by_stage.get('paiement', 0)
        stats['inscrits'] = by_stage.get('inscrit', 0)
        total = sum(by_stage.values())
        stats['total'] = total
        if stats['prospects'] > 0:
            stats['taux_conversion'] = round((stats['inscrits'] / max(stats['prospects'], 1)) * 100, 1)
        return stats, by_stage

    def _call_ai(system_prompt, user_prompt):
        try:
            if AI_CONFIG['provider'] == 'groq' and AI_CONFIG['groq_api_key']:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {AI_CONFIG['groq_api_key']}", "Content-Type": "application/json"},
                    json={"model": AI_CONFIG['groq_model'], "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ], "max_tokens": AI_CONFIG['max_tokens'], "temperature": AI_CONFIG['temperature']},
                    timeout=45
                )
                if r.status_code == 200:
                    return r.json()['choices'][0]['message']['content']
            elif AI_CONFIG['provider'] == 'openai' and AI_CONFIG['openai_api_key']:
                r = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {AI_CONFIG['openai_api_key']}", "Content-Type": "application/json"},
                    json={"model": AI_CONFIG['openai_model'], "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ], "max_tokens": AI_CONFIG['max_tokens'], "temperature": AI_CONFIG['temperature']},
                    timeout=45
                )
                if r.status_code == 200:
                    return r.json()['choices'][0]['message']['content']
        except Exception as e:
            print(f"Erreur IA admissions: {e}")
        return None

    def _evaluate_candidate_ai(candidat, documents):
        """Évaluation IA du dossier candidat."""
        docs_list = ", ".join(d['type_document'] for d in documents) or "Aucun document"
        system = """Tu es un expert admissions pour une école supérieure en Afrique.
Analyse le dossier et réponds UNIQUEMENT en JSON valide avec cette structure:
{"score": 75, "points_forts": ["...", "..."], "points_faibles": ["...", "..."]}
Score entre 0 et 100. Sois objectif et professionnel."""

        user = f"""Candidat: {candidat['prenom']} {candidat['nom']}
Programme: {candidat.get('programme_souhaite') or candidat.get('filiere_nom') or 'Non précisé'}
Pays: {candidat.get('pays')}
Documents fournis: {docs_list}
Notes internes: {candidat.get('notes') or 'Aucune'}"""

        raw = _call_ai(system, user)
        if raw:
            try:
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    return {
                        'score': min(100, max(0, int(data.get('score', 70)))),
                        'points_forts': data.get('points_forts', []),
                        'points_faibles': data.get('points_faibles', []),
                    }
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback intelligent sans IA
        nb_docs = len(documents)
        score = 50 + min(nb_docs * 8, 40)
        return {
            'score': score,
            'points_forts': ['Dossier reçu et en cours de traitement'] if nb_docs else ['Candidature enregistrée'],
            'points_faibles': ['Documents manquants'] if nb_docs < 3 else ['Analyse complémentaire recommandée'],
        }

    def _predict_enrollment(candidat, nb_comms, nb_docs, has_interview, has_payment):
        """Prédiction probabiliste d'inscription (premium)."""
        base = 20
        stage_weights = {
            'prospect': 10, 'demande_info': 20, 'candidature': 35,
            'documents_recus': 50, 'evaluation': 60, 'entretien': 70,
            'admis': 85, 'paiement': 92, 'inscrit': 100
        }
        base = stage_weights.get(candidat['statut'], 20)
        base += min(nb_docs * 5, 15)
        base += min(nb_comms * 3, 12)
        if has_interview:
            base += 10
        if has_payment:
            base += 15
        if candidat.get('score_ia'):
            base = int(base * 0.6 + candidat['score_ia'] * 0.4)
        return min(98, max(5, base + random.randint(-3, 3)))

    def _admissions_context(active_page='dashboard'):
        return {
            'stages': ADMISSIONS_STAGES,
            'stage_labels': STAGE_LABELS,
            'document_types': DOCUMENT_TYPES,
            'payment_methods': PAYMENT_METHODS,
            'lead_sources': LEAD_SOURCES,
            'active_page': active_page,
            'integrations': PaymentService.get_integrations_status(),
            'whatsapp_templates': WHATSAPP_TEMPLATES,
        }

    def _get_filieres(cursor, with_stats=False):
        if with_stats:
            cursor.execute("""
                SELECT f.id, f.nom, f.code, f.description, f.niveau, f.duree_annees,
                       COALESCE(c.frais_inscription, %s) as frais_inscription,
                       COALESCE(c.places_disponibles, 50) as places_disponibles,
                       COALESCE(c.est_ouvert, TRUE) as est_ouvert,
                       c.description_admission,
                       (SELECT COUNT(*) FROM admissions_candidats ac WHERE ac.filiere_id = f.id AND ac.school_id = f.school_id) as nb_candidats,
                       (SELECT COUNT(*) FROM admissions_candidats ac
                        WHERE ac.filiere_id = f.id AND ac.school_id = f.school_id AND ac.statut IN ('admis','paiement','inscrit')) as nb_admis,
                       (SELECT COUNT(*) FROM modules m WHERE m.filiere_id = f.id AND m.est_actif = TRUE) as nb_modules
                FROM filieres f
                LEFT JOIN admissions_filiere_config c ON c.filiere_id = f.id
                WHERE f.est_active = TRUE AND f.school_id = %s
                ORDER BY f.nom
            """, (INTEGRATIONS['admissions_fee_default'], tenant.current_school_id()))
        else:
            cursor.execute("""
                SELECT f.id, f.nom, f.code, f.niveau,
                       COALESCE(c.frais_inscription, %s) as frais_inscription,
                       COALESCE(c.est_ouvert, TRUE) as est_ouvert
                FROM filieres f
                LEFT JOIN admissions_filiere_config c ON c.filiere_id = f.id
                WHERE f.est_active = TRUE AND f.school_id = %s
                ORDER BY f.nom
            """, (INTEGRATIONS['admissions_fee_default'], tenant.current_school_id()))
        return cursor.fetchall()

    def _get_filiere_fee(cursor, filiere_id):
        if not filiere_id:
            return INTEGRATIONS['admissions_fee_default']
        cursor.execute("""
            SELECT COALESCE(c.frais_inscription, %s) as frais
            FROM filieres f
            LEFT JOIN admissions_filiere_config c ON c.filiere_id = f.id
            WHERE f.id = %s
        """, (INTEGRATIONS['admissions_fee_default'], filiere_id))
        row = cursor.fetchone()
        return row['frais'] if row else INTEGRATIONS['admissions_fee_default']

    def _ensure_payment_token(cursor, candidat_id):
        cursor.execute("SELECT payment_token FROM admissions_candidats WHERE id=%s", (candidat_id,))
        row = cursor.fetchone()
        token_val = row.get('payment_token') if isinstance(row, dict) else (row[0] if row else None)
        if token_val:
            return token_val
        token = PaymentService.generate_payment_token()
        cursor.execute("UPDATE admissions_candidats SET payment_token=%s WHERE id=%s AND school_id=%s", (token, candidat_id, tenant.current_school_id()))
        return token

    def _notify_whatsapp(cursor, candidat, template_key, extra_vars=None, custom_message=None):
        """Envoyer notification WhatsApp Business avec fallback wa.me."""
        vars_ = {
            'prenom': candidat.get('prenom', ''),
            'nom': candidat.get('nom', ''),
            'reference': candidat.get('reference', ''),
            'programme': candidat.get('programme_souhaite') or candidat.get('filiere_nom') or 'AdsClass',
            'payment_link': f"{INTEGRATIONS['app_base_url']}/apply/pay/{candidat.get('payment_token', '')}",
        }
        if extra_vars:
            vars_.update(extra_vars)

        if custom_message:
            result = WhatsAppService.send_text(candidat.get('telephone'), custom_message)
        else:
            result = WhatsAppService.send_template_message(candidat.get('telephone'), template_key, vars_)

        msg = custom_message or WHATSAPP_TEMPLATES.get(template_key, {}).get('body', '').format(**vars_)
        status = 'envoye' if result.get('success') else 'echec'
        provider = result.get('provider', 'wa_me_fallback' if result.get('fallback') else 'manual')
        cursor.execute("""
            INSERT INTO admissions_communications
            (candidat_id, canal, sujet, message, envoye_par, statut, wa_message_id, provider, delivery_status)
            VALUES (%s, 'whatsapp', %s, %s, %s, %s, %s, %s, %s)
        """, (
            candidat['id'], template_key or 'message_libre', msg[:2000],
            session.get('user_id'), status,
            result.get('message_id'), provider,
            'delivered' if result.get('success') else 'pending',
        ))
        return result

    # === ROUTES PUBLIQUES ===

    @app.route('/apply', methods=['GET', 'POST'])
    def public_apply():
        conn = get_db_connection()
        if not conn:
            flash("Service temporairement indisponible.", "danger")
            return render_template('apply.html', filieres=[], **_admissions_context())

        cursor = conn.cursor(dictionary=True)
        filieres = _get_filieres(cursor, with_stats=True)

        if request.method == 'POST':
            nom = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            email = request.form.get('email', '').strip().lower()
            telephone = request.form.get('telephone', '').strip()
            pays = request.form.get('pays', 'Niger').strip()
            filiere_id = request.form.get('filiere_id')
            programme = request.form.get('programme_souhaite', '').strip()
            source = request.form.get('source_lead', 'site_web')
            niveau = request.form.get('niveau', 'L1').strip()

            if not all([nom, prenom, email]):
                flash("Nom, prénom et email sont obligatoires.", "danger")
                cursor.close()
                conn.close()
                return render_template('apply.html', filieres=filieres, **_admissions_context())

            year_id = get_current_year_id()
            reference = _gen_reference()
            statut = 'candidature'
            payment_token = PaymentService.generate_payment_token()

            # Route publique : l'école est dérivée d'une source fiable, jamais de
            # la session ni d'un défaut implicite. Pour une candidature externe, la
            # filière choisie (school-scoped) est la source la plus fiable, puis le
            # host/sous-domaine, puis l'unique école active.
            school_id = None
            if filiere_id:
                cursor.execute("SELECT nom, school_id FROM filieres WHERE id=%s", (int(filiere_id),))
                fr = cursor.fetchone()
                if fr and fr.get('school_id'):
                    school_id = int(fr['school_id'])
            if not school_id:
                school_id = tenant.resolve_public_school_id(host=request.host)
            if not school_id:
                flash("Impossible de déterminer l'établissement : sélectionnez une filière.", "danger")
                cursor.close()
                conn.close()
                return render_template('apply.html', filieres=filieres, **_admissions_context())

            cursor.execute("""
                INSERT INTO admissions_candidats
                (reference, nom, prenom, email, telephone, pays, programme_souhaite,
                 filiere_id, niveau, source_lead, statut, annee_academique_id, payment_token, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (reference, nom, prenom, email, telephone, pays, programme or None,
                  int(filiere_id) if filiere_id else None, niveau, source, statut, year_id, payment_token, school_id))
            candidat_id = cursor.lastrowid

            # Auto-remplir programme depuis filière
            if filiere_id and not programme:
                if fr:
                    cursor.execute("UPDATE admissions_candidats SET programme_souhaite=%s WHERE id=%s AND school_id=%s",
                                   (fr['nom'], candidat_id, school_id))

            # Upload documents
            for doc_type, _ in DOCUMENT_TYPES:
                f = request.files.get(f'doc_{doc_type}')
                if f and f.filename and _allowed_file(f.filename):
                    ext = f.filename.rsplit('.', 1)[1].lower()
                    filename = secure_filename(f"{candidat_id}_{doc_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}")
                    path = os.path.join(UPLOAD_FOLDER, filename)
                    f.save(path)
                    cursor.execute("""
                        INSERT INTO admissions_documents (candidat_id, type_document, nom_fichier, chemin_fichier, taille_octets)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (candidat_id, doc_type, f.filename, path, os.path.getsize(path)))

            # Vérifier documents → statut
            cursor.execute("SELECT COUNT(*) as nb FROM admissions_documents WHERE candidat_id = %s", (candidat_id,))
            if cursor.fetchone()['nb'] >= 3:
                cursor.execute("UPDATE admissions_candidats SET statut = 'documents_recus' WHERE id = %s AND school_id = %s", (candidat_id, school_id))

            _log_historique(cursor, candidat_id, 'candidature_publique', None, statut, f'Réf: {reference}')

            # Notification WhatsApp automatique
            candidat_row = _get_candidat(cursor, candidat_id)
            if candidat_row and telephone:
                _notify_whatsapp(cursor, candidat_row, 'candidature_recue')

            conn.commit()
            cursor.close()
            conn.close()
            return render_template('apply_success.html', reference=reference, nom=prenom)

        cursor.close()
        conn.close()
        return render_template('apply.html', filieres=filieres, **_admissions_context())

    # === ROUTES ADMIN ===

    @app.route('/admin/admissions')
    @login_required
    @admin_required
    def admin_admissions():
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion.", "danger")
            return redirect(url_for('admin_home'))

        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        stats, by_stage = _get_stats(cursor, year_id)

        where = "WHERE c.annee_academique_id = %s AND c.school_id = %s" if year_id else "WHERE c.school_id = %s"
        params = (year_id, tenant.current_school_id()) if year_id else (tenant.current_school_id(),)
        cursor.execute(f"""
            SELECT c.id, c.reference, c.nom, c.prenom, c.email, c.telephone,
                   c.programme_souhaite, c.statut, c.score_ia, c.probabilite_inscription,
                   c.date_creation, f.nom as filiere_nom, f.code as filiere_code
            FROM admissions_candidats c
            LEFT JOIN filieres f ON c.filiere_id = f.id
            {where}
            ORDER BY c.date_modification DESC
        """, params)
        candidats = cursor.fetchall()

        pipeline = {stage: [] for stage in STAGE_CODES}
        for c in candidats:
            if c['statut'] in pipeline:
                pipeline[c['statut']].append(c)

        cursor.close()
        conn.close()
        ctx = _admissions_context('dashboard')
        return render_template('admissions/dashboard.html', stats=stats, pipeline=pipeline, **ctx)

    @app.route('/admin/admissions/prospects')
    @login_required
    @admin_required
    def admin_admissions_prospects():
        return _list_candidats('prospect', 'prospects', get_db_connection, get_current_year_id,
                               login_required, admin_required, render_template, redirect, url_for, flash, _admissions_context)

    @app.route('/admin/admissions/candidatures')
    @login_required
    @admin_required
    def admin_admissions_candidatures():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        where = "WHERE statut IN ('candidature','documents_recus','evaluation') AND school_id = %s "
        if year_id:
            where += "AND annee_academique_id = %s "
            params = (tenant.current_school_id(), year_id)
        else:
            params = (tenant.current_school_id(),)
        cursor.execute(f"SELECT * FROM admissions_candidats {where} ORDER BY date_creation DESC", params)
        candidats = cursor.fetchall()
        cursor.close()
        conn.close()
        ctx = _admissions_context('candidatures')
        return render_template('admissions/list.html', candidats=candidats, page_title='Candidatures', **ctx)

    @app.route('/admin/admissions/entretiens')
    @login_required
    @admin_required
    def admin_admissions_entretiens():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.*, c.nom, c.prenom, c.email, c.telephone, c.reference
            FROM admissions_entretiens e
            JOIN admissions_candidats c ON e.candidat_id = c.id
            WHERE e.date_entretien >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND c.school_id = %s
            ORDER BY e.date_entretien ASC
        """, (tenant.current_school_id(),))
        entretiens = cursor.fetchall()
        cursor.execute("""
            SELECT id, nom, prenom, reference, statut FROM admissions_candidats
            WHERE statut IN ('evaluation','entretien','candidature','documents_recus','admis')
            AND school_id = %s
            ORDER BY nom
        """, (tenant.current_school_id(),))
        candidats_disponibles = cursor.fetchall()
        cursor.close()
        conn.close()
        ctx = _admissions_context('entretiens')
        return render_template('admissions/entretiens.html', entretiens=entretiens,
                               candidats=candidats_disponibles, **ctx)

    @app.route('/admin/admissions/documents')
    @login_required
    @admin_required
    def admin_admissions_documents():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT d.*, c.nom, c.prenom, c.reference, c.statut
            FROM admissions_documents d
            JOIN admissions_candidats c ON d.candidat_id = c.id
            WHERE c.school_id = %s
            ORDER BY d.date_upload DESC
            LIMIT 200
        """, (tenant.current_school_id(),))
        documents = cursor.fetchall()
        cursor.close()
        conn.close()
        ctx = _admissions_context('documents')
        return render_template('admissions/documents.html', documents=documents, **ctx)

    @app.route('/admin/admissions/paiements')
    @login_required
    @admin_required
    def admin_admissions_paiements():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, c.nom, c.prenom, c.reference, c.email
            FROM admissions_paiements p
            JOIN admissions_candidats c ON p.candidat_id = c.id
            WHERE c.school_id = %s
            ORDER BY p.date_paiement DESC
        """, (tenant.current_school_id(),))
        paiements = cursor.fetchall()
        cursor.execute("""
            SELECT id, nom, prenom, reference FROM admissions_candidats
            WHERE statut IN ('admis','paiement','entretien') AND school_id = %s ORDER BY nom
        """, (tenant.current_school_id(),))
        candidats = cursor.fetchall()
        cursor.execute("""
            SELECT COALESCE(SUM(p.montant), 0) as total,
                   SUM(CASE WHEN p.statut='confirme' THEN p.montant ELSE 0 END) as confirme
            FROM admissions_paiements p
            JOIN admissions_candidats c ON p.candidat_id = c.id
            WHERE c.school_id = %s
        """, (tenant.current_school_id(),))
        totaux = cursor.fetchone()
        cursor.close()
        conn.close()
        ctx = _admissions_context('paiements')
        return render_template('admissions/paiements.html', paiements=paiements,
                               candidats=candidats, totaux=totaux, **ctx)

    @app.route('/admin/admissions/statistiques')
    @login_required
    @admin_required
    def admin_admissions_stats():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        stats, by_stage = _get_stats(cursor, year_id)

        cursor.execute("""
            SELECT source_lead, COUNT(*) as nb FROM admissions_candidats
            WHERE school_id = %s
            GROUP BY source_lead ORDER BY nb DESC
        """, (tenant.current_school_id(),))
        by_source = cursor.fetchall()

        cursor.execute("""
            SELECT f.nom as filiere, f.code, COUNT(c.id) as nb,
                   SUM(CASE WHEN c.statut='inscrit' THEN 1 ELSE 0 END) as inscrits
            FROM filieres f
            LEFT JOIN admissions_candidats c ON c.filiere_id = f.id AND c.school_id = %s
            WHERE f.est_active = TRUE AND f.school_id = %s
            GROUP BY f.id, f.nom, f.code
            ORDER BY nb DESC
        """, (tenant.current_school_id(), tenant.current_school_id()))
        by_filiere = cursor.fetchall()

        cursor.execute("""
            SELECT COALESCE(f.nom, c.programme_souhaite, 'Non précisé') as programme, COUNT(*) as nb
            FROM admissions_candidats c
            LEFT JOIN filieres f ON c.filiere_id = f.id
            WHERE c.school_id = %s
            GROUP BY COALESCE(f.nom, c.programme_souhaite, 'Non précisé')
            ORDER BY nb DESC LIMIT 10
        """, (tenant.current_school_id(),))
        by_programme = cursor.fetchall()

        cursor.execute("""
            SELECT c.id, c.nom, c.prenom, c.probabilite_inscription, c.statut, c.score_ia
            FROM admissions_candidats c
            WHERE c.statut NOT IN ('inscrit', 'prospect') AND c.school_id = %s
            ORDER BY c.probabilite_inscription IS NULL, c.probabilite_inscription DESC, c.score_ia DESC
            LIMIT 15
        """, (tenant.current_school_id(),))
        predictions = cursor.fetchall()

        cursor.close()
        conn.close()
        ctx = _admissions_context('statistiques')
        return render_template('admissions/statistiques.html', stats=stats, by_stage=by_stage,
                               by_source=by_source, by_programme=by_programme, by_filiere=by_filiere,
                               predictions=predictions, **ctx)

    @app.route('/admin/admissions/candidat/<int:candidat_id>')
    @login_required
    @admin_required
    def admin_admissions_candidat_detail(candidat_id):
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion.", "danger")
            return redirect(url_for('admin_admissions'))

        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        if not candidat:
            flash("Candidat introuvable.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for('admin_admissions'))

        cursor.execute("SELECT * FROM admissions_documents WHERE candidat_id = %s ORDER BY date_upload DESC", (candidat_id,))
        documents = cursor.fetchall()
        cursor.execute("SELECT * FROM admissions_communications WHERE candidat_id = %s ORDER BY date_envoi DESC", (candidat_id,))
        communications = cursor.fetchall()
        cursor.execute("SELECT * FROM admissions_entretiens WHERE candidat_id = %s ORDER BY date_entretien DESC", (candidat_id,))
        entretiens = cursor.fetchall()
        cursor.execute("SELECT * FROM admissions_paiements WHERE candidat_id = %s ORDER BY date_paiement DESC", (candidat_id,))
        paiements = cursor.fetchall()
        cursor.execute("SELECT * FROM admissions_historique WHERE candidat_id = %s ORDER BY date_action DESC LIMIT 30", (candidat_id,))
        historique = cursor.fetchall()
        cursor.execute("SELECT id, nom, code FROM filieres WHERE est_active = TRUE AND school_id = %s ORDER BY nom", (tenant.current_school_id(),))
        filieres = cursor.fetchall()

        cursor.close()
        conn.close()

        points_forts = []
        points_faibles = []
        if candidat.get('points_forts_ia'):
            try:
                points_forts = json.loads(candidat['points_forts_ia'])
            except (json.JSONDecodeError, TypeError):
                pass
        if candidat.get('points_faibles_ia'):
            try:
                points_faibles = json.loads(candidat['points_faibles_ia'])
            except (json.JSONDecodeError, TypeError):
                pass

        ctx = _admissions_context('candidat')
        return render_template('admissions/candidat_detail.html', candidat=candidat,
                               documents=documents, communications=communications,
                               entretiens=entretiens, paiements=paiements,
                               historique=historique, filieres=filieres,
                               points_forts=points_forts, points_faibles=points_faibles, **ctx)

    @app.route('/admin/admissions/candidat/create', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_create_candidat():
        conn = get_db_connection()
        cursor = conn.cursor()
        reference = _gen_reference()
        cursor.execute("""
            INSERT INTO admissions_candidats
            (reference, nom, prenom, email, telephone, pays, programme_souhaite,
             source_lead, statut, annee_academique_id, cree_par, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            reference,
            request.form.get('nom', '').strip(),
            request.form.get('prenom', '').strip(),
            request.form.get('email', '').strip(),
            request.form.get('telephone', ''),
            request.form.get('pays', 'Niger'),
            request.form.get('programme_souhaite', ''),
            request.form.get('source_lead', 'autre'),
            request.form.get('statut', 'prospect'),
            get_current_year_id(),
            session.get('user_id'),
            tenant.current_school_id(),
        ))
        cid = cursor.lastrowid
        _log_historique(cursor, cid, 'creation_manuelle', None, request.form.get('statut', 'prospect'))
        conn.commit()
        cursor.close()
        conn.close()
        flash(f"Prospect créé — Réf. {reference}", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=cid))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/update', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_update_candidat(candidat_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        old = _get_candidat(cursor, candidat_id)
        cursor.execute("""
            UPDATE admissions_candidats SET
                nom=%s, prenom=%s, email=%s, telephone=%s, pays=%s,
                programme_souhaite=%s, filiere_id=%s, niveau=%s,
                source_lead=%s, notes=%s
            WHERE id=%s AND school_id=%s
        """, (
            request.form.get('nom'), request.form.get('prenom'), request.form.get('email'),
            request.form.get('telephone'), request.form.get('pays'),
            request.form.get('programme_souhaite'),
            request.form.get('filiere_id') or None,
            request.form.get('niveau', 'L1'),
            request.form.get('source_lead'),
            request.form.get('notes'),
            candidat_id, tenant.current_school_id(),
        ))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Fiche candidat mise à jour.", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/evaluate-ai', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_evaluate_ai(candidat_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        cursor.execute("SELECT * FROM admissions_documents WHERE candidat_id = %s", (candidat_id,))
        documents = cursor.fetchall()
        result = _evaluate_candidate_ai(candidat, documents)

        cursor.execute("""
            UPDATE admissions_candidats SET
                score_ia=%s, points_forts_ia=%s, points_faibles_ia=%s,
                statut=CASE WHEN statut IN ('candidature','documents_recus') THEN 'evaluation' ELSE statut END
            WHERE id=%s AND school_id=%s
        """, (
            result['score'],
            json.dumps(result['points_forts'], ensure_ascii=False),
            json.dumps(result['points_faibles'], ensure_ascii=False),
            candidat_id, tenant.current_school_id(),
        ))
        _log_historique(cursor, candidat_id, 'evaluation_ia', candidat['statut'], 'evaluation',
                        f"Score IA: {result['score']}/100")
        conn.commit()
        cursor.close()
        conn.close()
        flash(f"Évaluation IA terminée — Score: {result['score']}/100", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/predict', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_predict(candidat_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        cursor.execute("SELECT COUNT(*) as nb FROM admissions_communications WHERE candidat_id=%s", (candidat_id,))
        nb_comms = cursor.fetchone()['nb']
        cursor.execute("SELECT COUNT(*) as nb FROM admissions_documents WHERE candidat_id=%s", (candidat_id,))
        nb_docs = cursor.fetchone()['nb']
        cursor.execute("SELECT COUNT(*) as nb FROM admissions_entretiens WHERE candidat_id=%s AND statut!='annule'", (candidat_id,))
        has_interview = cursor.fetchone()['nb'] > 0
        cursor.execute("SELECT COUNT(*) as nb FROM admissions_paiements WHERE candidat_id=%s AND statut='confirme'", (candidat_id,))
        has_payment = cursor.fetchone()['nb'] > 0

        prob = _predict_enrollment(candidat, nb_comms, nb_docs, has_interview, has_payment)
        cursor.execute("UPDATE admissions_candidats SET probabilite_inscription=%s WHERE id=%s AND school_id=%s", (prob, candidat_id, tenant.current_school_id()))
        _log_historique(cursor, candidat_id, 'prediction_ia', None, None, f"Probabilité: {prob}%")
        conn.commit()
        cursor.close()
        conn.close()
        flash(f"Probabilité d'inscription estimée: {prob}%", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    def _try_enroll_candidat(conn, candidat_id):
        """Crée le compte étudiant si pas encore fait. Retourne le dict credentials ou None."""
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        if not candidat or candidat.get('etudiant_id'):
            cursor.close()
            return None
        old_statut = candidat['statut']
        result = enrollir_candidat_en_etudiant(conn, candidat, generate_password_hash, candidat_id)
        _log_historique(
            cursor, candidat_id, 'conversion_etudiant', old_statut, 'inscrit',
            f"Identifiant: {result['identifiant']}"
        )
        conn.commit()
        cursor.close()
        return result

    @app.route('/admin/admissions/candidat/<int:candidat_id>/convert', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_convert_student(candidat_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        cursor.close()

        if not candidat:
            flash("Candidat introuvable.", "danger")
            return redirect(url_for('admin_admissions'))

        if candidat.get('etudiant_id'):
            flash("Ce candidat est déjà converti en étudiant.", "warning")
            return redirect(url_for('admin_student_credentials_print', etudiant_id=candidat['etudiant_id']))

        try:
            result = _try_enroll_candidat(conn, candidat_id)
            conn.close()
            flash(f"✓ {result['prenom']} {result['nom']} inscrit(e) — classe {result['classe']}", "success")
            return redirect(url_for('admin_student_credentials_print', etudiant_id=result['etudiant_id']))
        except ValueError as e:
            conn.close()
            flash(str(e), "danger")
            return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))
        except Exception as e:
            conn.close()
            flash(f"Erreur lors de l'inscription: {e}", "danger")
            return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/communicate', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_communicate(candidat_id):
        canal = request.form.get('canal', 'email')
        sujet = request.form.get('sujet', '')
        message = request.form.get('message', '').strip()
        template_key = request.form.get('template_key', '')
        if not message and not template_key:
            flash("Le message ou un modèle est requis.", "danger")
            return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        _ensure_payment_token(cursor, candidat_id)
        candidat = _get_candidat(cursor, candidat_id)

        if canal == 'whatsapp':
            result = _notify_whatsapp(cursor, candidat, template_key or None,
                                      custom_message=message if message else None)
            _log_historique(cursor, candidat_id, 'communication_whatsapp', None, None,
                            f"WA: {result.get('message_id', 'fallback')}")
            conn.commit()
            cursor.close()
            conn.close()
            if result.get('success'):
                flash("✓ Message WhatsApp envoyé via Business API.", "success")
            elif result.get('wa_me_url'):
                flash("API non disponible — ouverture WhatsApp Web...", "warning")
                return redirect(result['wa_me_url'])
            else:
                flash(f"Erreur WhatsApp: {result.get('error')}", "danger")
            return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

        cursor.execute("""
            INSERT INTO admissions_communications (candidat_id, canal, sujet, message, envoye_par, provider)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (candidat_id, canal, sujet, message, session.get('user_id'), 'manual'))
        _log_historique(cursor, candidat_id, f'communication_{canal}', None, None, sujet or message[:80])
        conn.commit()
        cursor.close()
        conn.close()

        if canal == 'email':
            mailto = f"mailto:{candidat['email']}?subject={requests.utils.quote(sujet)}&body={requests.utils.quote(message)}"
            flash("Communication enregistrée.", "success")
            return redirect(mailto)

        flash(f"Communication {canal.upper()} enregistrée.", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    @app.route('/admin/admissions/entretien/create', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_create_entretien():
        candidat_id = request.form.get('candidat_id')
        date_str = request.form.get('date_entretien')
        heure = request.form.get('heure', '09:00')
        dt = datetime.strptime(f"{date_str} {heure}", "%Y-%m-%d %H:%M")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO admissions_entretiens
            (candidat_id, date_entretien, duree_minutes, type_entretien, lieu, intervieweur_id, invitation_envoyee)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        """, (
            candidat_id, dt,
            int(request.form.get('duree_minutes', 30)),
            request.form.get('type_entretien', 'presentiel'),
            request.form.get('lieu', 'Campus AdsClass'),
            session.get('user_id'),
        ))
        cursor.execute("UPDATE admissions_candidats SET statut='entretien' WHERE id=%s AND statut NOT IN ('admis','paiement','inscrit') AND school_id=%s", (candidat_id, tenant.current_school_id()))
        _log_historique(cursor, int(candidat_id), 'entretien_planifie', None, 'entretien', dt.strftime('%d/%m/%Y %H:%M'))

        candidat = _get_candidat(cursor, int(candidat_id))
        if candidat:
            _ensure_payment_token(cursor, int(candidat_id))
            candidat = _get_candidat(cursor, int(candidat_id))
            _notify_whatsapp(cursor, candidat, 'entretien_planifie', {
                'date': dt.strftime('%d/%m/%Y'),
                'heure': dt.strftime('%H:%M'),
                'lieu': request.form.get('lieu', 'Campus AdsClass'),
            })

        conn.commit()
        cursor.close()
        conn.close()
        flash("Entretien planifié — invitation WhatsApp envoyée.", "success")
        return redirect(url_for('admin_admissions_entretiens'))

    @app.route('/admin/admissions/paiement/create', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_create_paiement():
        conn = get_db_connection()
        cursor = conn.cursor()
        candidat_id = request.form.get('candidat_id')
        montant = float(request.form.get('montant', 0))
        moyen = request.form.get('moyen_paiement', 'especes')
        statut = request.form.get('statut', 'confirme')
        ref = request.form.get('reference_transaction', '')

        cursor.execute("""
            INSERT INTO admissions_paiements
            (candidat_id, montant, moyen_paiement, reference_transaction, statut, confirme_par, observation)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (candidat_id, montant, moyen, ref, statut, session.get('user_id'),
              request.form.get('observation', '')))

        if statut == 'confirme':
            cursor.execute("UPDATE admissions_candidats SET statut='paiement' WHERE id=%s AND statut IN ('admis','entretien','evaluation') AND school_id=%s", (candidat_id, tenant.current_school_id()))
        _log_historique(cursor, int(candidat_id), 'paiement_enregistre', None, 'paiement', f"{montant} FCFA via {moyen}")
        conn.commit()
        cursor.close()
        conn.close()
        flash("Paiement enregistré.", "success")
        return redirect(url_for('admin_admissions_paiements'))

    @app.route('/api/admissions/move-stage', methods=['POST'])
    @login_required
    @admin_required
    def api_admissions_move_stage():
        data = request.get_json() or {}
        candidat_id = data.get('candidat_id')
        new_stage = data.get('statut')
        if new_stage not in STAGE_CODES:
            return jsonify({'success': False, 'error': 'Statut invalide'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        old = _get_candidat(cursor, candidat_id)
        if not old:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Candidat introuvable'}), 404

        cursor.execute("UPDATE admissions_candidats SET statut=%s WHERE id=%s AND school_id=%s", (new_stage, candidat_id, tenant.current_school_id()))
        _log_historique(cursor, candidat_id, 'changement_pipeline', old['statut'], new_stage)

        enroll_result = None
        if new_stage == 'inscrit' and not old.get('etudiant_id'):
            try:
                enroll_result = _try_enroll_candidat(conn, candidat_id)
            except ValueError as e:
                conn.rollback()
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'error': str(e)}), 400

        conn.commit()
        cursor.close()
        conn.close()
        resp = {'success': True, 'statut': new_stage, 'label': STAGE_LABELS[new_stage]}
        if enroll_result:
            resp['etudiant_id'] = enroll_result['etudiant_id']
            resp['credentials_url'] = f"/admin/student/{enroll_result['etudiant_id']}/credentials/print"
        return jsonify(resp)

    @app.route('/admin/admissions/document/<int:doc_id>/download')
    @login_required
    @admin_required
    def admin_admissions_download_document(doc_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT chemin_fichier, nom_fichier FROM admissions_documents WHERE id=%s", (doc_id,))
        doc = cursor.fetchone()
        cursor.close()
        conn.close()
        if not doc or not os.path.exists(doc['chemin_fichier']):
            flash("Document introuvable.", "danger")
            return redirect(url_for('admin_admissions_documents'))
        directory = os.path.dirname(doc['chemin_fichier'])
        filename = os.path.basename(doc['chemin_fichier'])
        return send_from_directory(directory, filename, as_attachment=True, download_name=doc['nom_fichier'])

    # === FILIÈRES ADMISSIONS ===

    @app.route('/admin/admissions/filieres')
    @login_required
    @admin_required
    def admin_admissions_filieres():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        filieres = _get_filieres(cursor, with_stats=True)
        cursor.close()
        conn.close()
        ctx = _admissions_context('filieres')
        return render_template('admissions/filieres.html', filieres=filieres, **ctx)

    @app.route('/admin/admissions/filieres/<int:filiere_id>/update', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_filiere_update(filiere_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        frais = float(request.form.get('frais_inscription', INTEGRATIONS['admissions_fee_default']))
        places = int(request.form.get('places_disponibles', 50))
        ouvert = request.form.get('est_ouvert') == 'on'
        desc = request.form.get('description_admission', '')
        cursor.execute("""
            INSERT INTO admissions_filiere_config (filiere_id, frais_inscription, places_disponibles, est_ouvert, description_admission)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE frais_inscription=%s, places_disponibles=%s, est_ouvert=%s, description_admission=%s
        """, (filiere_id, frais, places, ouvert, desc, frais, places, ouvert, desc))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Configuration filière mise à jour.", "success")
        return redirect(url_for('admin_admissions_filieres'))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/admit', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_admit_candidat(candidat_id):
        """Marquer admis + envoyer lien paiement WhatsApp."""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidat = _get_candidat(cursor, candidat_id)
        token = _ensure_payment_token(cursor, candidat_id)
        candidat = _get_candidat(cursor, candidat_id)
        cursor.execute("UPDATE admissions_candidats SET statut='admis' WHERE id=%s AND school_id=%s", (candidat_id, tenant.current_school_id()))
        _log_historique(cursor, candidat_id, 'admission_accordee', candidat['statut'], 'admis')
        _notify_whatsapp(cursor, candidat, 'admis_felicitations')
        conn.commit()
        cursor.close()
        conn.close()
        pay_url = f"{INTEGRATIONS['app_base_url']}/apply/pay/{token}"
        flash(f"Candidat admis — Lien paiement: {pay_url}", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    @app.route('/admin/admissions/candidat/<int:candidat_id>/payment-link', methods=['POST'])
    @login_required
    @admin_required
    def admin_admissions_payment_link(candidat_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        token = _ensure_payment_token(cursor, candidat_id)
        conn.commit()
        cursor.close()
        conn.close()
        url = f"{INTEGRATIONS['app_base_url']}/apply/pay/{token}"
        flash(f"Lien de paiement généré: {url}", "success")
        return redirect(url_for('admin_admissions_candidat_detail', candidat_id=candidat_id))

    # === PAIEMENTS EN LIGNE (PUBLIC) ===

    @app.route('/apply/pay/<token>')
    def public_payment_page(token):
        conn = get_db_connection()
        if not conn:
            return render_template('admissions/payment_error.html', error="Service indisponible")
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT c.*, f.nom as filiere_nom
            FROM admissions_candidats c
            LEFT JOIN filieres f ON c.filiere_id = f.id
            WHERE c.payment_token = %s
        """, (token,))
        candidat = cursor.fetchone()
        if not candidat:
            cursor.close()
            conn.close()
            return render_template('admissions/payment_error.html', error="Lien de paiement invalide ou expiré")

        montant = _get_filiere_fee(cursor, candidat.get('filiere_id'))
        cursor.execute("""
            SELECT * FROM admissions_paiements WHERE candidat_id=%s AND statut='confirme' LIMIT 1
        """, (candidat['id'],))
        deja_paye = cursor.fetchone()
        cursor.close()
        conn.close()

        return render_template('admissions/payment_checkout.html',
                               candidat=candidat, montant=montant, token=token,
                               deja_paye=deja_paye,
                               stripe_ok=is_stripe_configured(),
                               flutterwave_ok=is_flutterwave_configured())

    @app.route('/apply/pay/<token>/stripe', methods=['POST'])
    def public_payment_stripe(token):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT c.*, f.nom as filiere_nom FROM admissions_candidats c LEFT JOIN filieres f ON c.filiere_id=f.id WHERE c.payment_token=%s", (token,))
        candidat = cursor.fetchone()
        if not candidat:
            flash("Lien invalide.", "danger")
            return redirect(url_for('public_apply'))

        montant = _get_filiere_fee(cursor, candidat.get('filiere_id'))
        cursor.execute("""
            INSERT INTO admissions_paiements (candidat_id, montant, moyen_paiement, statut, provider)
            VALUES (%s, %s, 'stripe', 'en_attente', 'stripe')
        """, (candidat['id'], montant))
        payment_id = cursor.lastrowid
        conn.commit()

        result = PaymentService.create_stripe_checkout(
            candidat, montant, payment_id,
            f"Frais inscription — {candidat.get('filiere_nom') or 'AdsClass'}"
        )
        if result.get('success'):
            cursor.execute("""
                UPDATE admissions_paiements SET stripe_session_id=%s, payment_url=%s, reference_transaction=%s WHERE id=%s
            """, (result['session_id'], result['payment_url'], result['session_id'], payment_id))
            conn.commit()
            cursor.close()
            conn.close()
            return redirect(result['payment_url'])
        cursor.close()
        conn.close()
        flash(f"Erreur Stripe: {result.get('error')}", "danger")
        return redirect(url_for('public_payment_page', token=token))

    @app.route('/apply/pay/<token>/mobile', methods=['POST'])
    def public_payment_mobile(token):
        provider = request.form.get('provider', 'mobilemoney')
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT c.*, f.nom as filiere_nom FROM admissions_candidats c LEFT JOIN filieres f ON c.filiere_id=f.id WHERE c.payment_token=%s", (token,))
        candidat = cursor.fetchone()
        if not candidat:
            flash("Lien invalide.", "danger")
            return redirect(url_for('public_apply'))

        montant = _get_filiere_fee(cursor, candidat.get('filiere_id'))
        moyen_map = {'orange_money': 'orange_money', 'airtel_money': 'airtel_money', 'mobilemoney': 'mobilemoney'}
        moyen = moyen_map.get(provider, 'mobilemoney')

        cursor.execute("""
            INSERT INTO admissions_paiements (candidat_id, montant, moyen_paiement, statut, provider)
            VALUES (%s, %s, %s, 'en_attente', 'flutterwave')
        """, (candidat['id'], montant, moyen))
        payment_id = cursor.lastrowid
        conn.commit()

        result = PaymentService.create_flutterwave_payment(candidat, montant, payment_id, moyen)
        if result.get('success'):
            cursor.execute("""
                UPDATE admissions_paiements SET flutterwave_tx_ref=%s, payment_url=%s, reference_transaction=%s WHERE id=%s
            """, (result['tx_ref'], result['payment_url'], result['tx_ref'], payment_id))
            conn.commit()
            cursor.close()
            conn.close()
            return redirect(result['payment_url'])
        cursor.close()
        conn.close()
        flash(f"Erreur paiement mobile: {result.get('error')}", "danger")
        return redirect(url_for('public_payment_page', token=token))

    @app.route('/apply/pay/<token>/success')
    def public_payment_success(token):
        provider = request.args.get('provider', '')
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM admissions_candidats WHERE payment_token=%s", (token,))
        candidat = cursor.fetchone()
        if not candidat:
            cursor.close()
            conn.close()
            return render_template('admissions/payment_error.html', error="Candidat introuvable")

        paid = False
        reference = ''
        if provider == 'stripe':
            session_id = request.args.get('session_id', '')
            verify = PaymentService.verify_stripe_session(session_id)
            if verify.get('paid'):
                paid = True
                reference = verify.get('reference', session_id)
                cursor.execute("""
                    UPDATE admissions_paiements SET statut='confirme', reference_transaction=%s, webhook_data=%s
                    WHERE stripe_session_id=%s AND candidat_id=%s
                """, (reference, json.dumps(verify), session_id, candidat['id']))
        elif provider == 'flutterwave':
            tx_ref = request.args.get('tx_ref', '')
            verify = PaymentService.verify_flutterwave_transaction(tx_ref)
            if verify.get('paid'):
                paid = True
                reference = verify.get('reference', tx_ref)
                cursor.execute("""
                    UPDATE admissions_paiements SET statut='confirme', reference_transaction=%s, webhook_data=%s
                    WHERE flutterwave_tx_ref=%s AND candidat_id=%s
                """, (reference, json.dumps(verify), tx_ref, candidat['id']))

        if paid:
            cursor.execute("UPDATE admissions_candidats SET statut='paiement' WHERE id=%s AND school_id=%s", (candidat['id'], candidat['school_id']))
            _log_historique(cursor, candidat['id'], 'paiement_en_ligne', candidat['statut'], 'paiement', reference)
            candidat_full = _get_candidat(cursor, candidat['id'])
            _notify_whatsapp(cursor, candidat_full, 'paiement_confirme', {
                'montant': int(_get_filiere_fee(cursor, candidat.get('filiere_id'))),
                'reference': reference,
            })
        conn.commit()
        cursor.close()
        conn.close()
        return render_template('admissions/payment_success.html', candidat=candidat, paid=paid, reference=reference)

    @app.route('/api/admissions/webhook/stripe', methods=['POST'])
    def webhook_stripe():
        payload = request.get_data()
        sig = request.headers.get('Stripe-Signature', '')
        result = PaymentService.verify_stripe_webhook(payload, sig)
        if not result.get('valid'):
            return jsonify({'error': result.get('error')}), 400
        event = result['event']
        if event.get('type') == 'checkout.session.completed':
            sess = event['data']['object']
            meta = sess.get('metadata', {})
            candidat_id = meta.get('candidat_id')
            payment_id = meta.get('payment_id')
            if candidat_id and payment_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE admissions_paiements SET statut='confirme', webhook_data=%s WHERE id=%s
                """, (json.dumps(sess), payment_id))
                cursor.execute("""
                    UPDATE admissions_candidats c
                    JOIN admissions_paiements p ON p.candidat_id = c.id
                    SET c.statut='paiement' WHERE c.id=%s AND p.id=%s
                """, (candidat_id, payment_id))
                conn.commit()
                cursor.close()
                conn.close()
        return jsonify({'received': True})

    @app.route('/api/admissions/webhook/flutterwave', methods=['POST'])
    def webhook_flutterwave():
        data = request.get_json() or {}
        if data.get('event') == 'charge.completed' and data.get('data', {}).get('status') == 'successful':
            tx_ref = data['data'].get('tx_ref', '')
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE admissions_paiements SET statut='confirme', webhook_data=%s
                WHERE flutterwave_tx_ref=%s
            """, (json.dumps(data), tx_ref))
            cursor.execute("""
                UPDATE admissions_candidats c
                JOIN admissions_paiements p ON p.candidat_id = c.id
                SET c.statut='paiement' WHERE p.flutterwave_tx_ref=%s
            """, (tx_ref,))
            conn.commit()
            cursor.close()
            conn.close()
        return jsonify({'status': 'ok'})

    @app.route('/admin/admissions/integrations')
    @login_required
    @admin_required
    def admin_admissions_integrations():
        status = PaymentService.get_integrations_status()
        ctx = _admissions_context('integrations')
        return render_template('admissions/integrations.html', status=status, config=INTEGRATIONS, **ctx)


def _list_candidats(statut_filter, page_key, get_db, get_year, login_req, admin_req, render, redirect, url_for, flash, ctx_fn):
    """Helper pour lister prospects."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    year_id = get_year()
    where = "WHERE statut IN ('prospect','demande_info') AND school_id = %s "
    if year_id:
        where += "AND annee_academique_id = %s "
        params = (tenant.current_school_id(), year_id)
    else:
        params = (tenant.current_school_id(),)
    cursor.execute(f"SELECT * FROM admissions_candidats {where} ORDER BY date_creation DESC", params)
    candidats = cursor.fetchall()
    cursor.close()
    conn.close()
    return render('admissions/list.html', candidats=candidats, page_title='Prospects', **ctx_fn(page_key))
