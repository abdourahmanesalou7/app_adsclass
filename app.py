from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import mysql.connector
from mysql.connector import Error
from functools import wraps
import io
from io import BytesIO
import csv
import os
from datetime import datetime, timedelta

# Charger les variables d'environnement depuis .env / .env.local (clé Groq, etc.)
def _load_dotenv():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for env_file in ('.env', '.env.local'):
        env_path = os.path.join(base_dir, env_file)
        if not os.path.exists(env_path):
            continue
        override = env_file == '.env.local'
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and (override or key not in os.environ):
                    os.environ[key] = value

_load_dotenv()
import hashlib
import secrets
import json
import qrcode
import base64

# Import du système de permissions
from permissions import (
    PermissionManager,
    require_permission,
    require_any_permission,
    require_all_permissions,
    check_permission,
    get_current_user_role,
    get_current_user_permissions
)

try:
    from notification_services import (
        notify_absence_marked,
        notify_admin_absence_alert,
        notify_course_cancelled,
    )
except ImportError:
    notify_absence_marked = notify_admin_absence_alert = notify_course_cancelled = None

from imports.credentials import enrich_user_payload
from student_enrollment_service import (
    ensure_student_account_columns,
    generer_nom_classe,
    get_active_filieres,
    get_canonical_active_filieres,
    normaliser_niveau,
    niveau_canonique,
    niveau_aliases,
    filiere_aliases,
    sync_enrollments_for_student,
    sync_enrollments_for_course,
    remove_course_enrollments,
    build_filiere_niveau_where,
    NIVEAU_SHORT_TO_LONG,
    build_classes_par_filiere,
    get_students_for_class,
    resolve_filiere_by_name,
    sync_legacy_student_filieres,
    standardize_filieres_niveaux,
    student_enrollment_join_sql,
    student_course_tenant_where,
)

app = Flask(__name__)

# --- Pilotage debug/production par variable d'environnement (P1) ---
def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ('1', 'true', 'yes', 'on')

DEBUG_MODE = _env_bool('FLASK_DEBUG', False)

# --- SECRET_KEY : obligatoire en production, aleatoire en developpement (P1.1) ---
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if DEBUG_MODE:
        _secret_key = secrets.token_hex(32)
        print("[config] FLASK_DEBUG=True : SECRET_KEY temporaire generee "
              "(sessions invalidees a chaque redemarrage).")
    else:
        raise RuntimeError(
            "SECRET_KEY manquante. Definissez SECRET_KEY dans .env "
            "(obligatoire quand FLASK_DEBUG=False)."
        )
app.secret_key = _secret_key

# --- Sécurité des cookies de session (P1.3) ---
# SECURE=True hors debug (HTTPS requis en production), assoupli en dev local (HTTP).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=not DEBUG_MODE,
)

# Enregistrer les fonctions de permission pour les templates Jinja2
app.jinja_env.globals['check_permission'] = check_permission
app.jinja_env.globals['get_current_user_role'] = get_current_user_role
app.jinja_env.globals['get_current_user_permissions'] = get_current_user_permissions

# Module d'import ETL (Excel/CSV/Word/MySQL/API) — Blueprint
try:
    from imports.routes import bp as imports_bp
    from imports.db_init import ensure_import_tables
    ensure_import_tables()
    app.register_blueprint(imports_bp)
except Exception as _imp_err:
    print(f"[app] Module imports non chargé : {_imp_err}")

# Multi-tenant (school_id) — Phase 3
try:
    import tenant
    tenant.init_app(app)
except Exception as _tn_err:
    print(f"[app] Module tenant non chargé : {_tn_err}")

# RBAC multi-tenant (rôles spécifiques par école) — migration idempotente
try:
    from init_rbac_multitenant import ensure_rbac_multitenant
    ensure_rbac_multitenant()
except Exception as _rbac_err:
    print(f"[app] Migration RBAC multi-tenant non appliquée : {_rbac_err}")

# SaaS Subscriptions — Phase 4
try:
    from services import subscriptions
    from routes.subscriptions import bp as subscription_bp
    app.register_blueprint(subscription_bp)
    subscriptions.init_app(app)
except Exception as _sub_err:
    print(f"[app] Module subscriptions non chargé : {_sub_err}")

# Portail Superadmin — Phase 5
try:
    from superadmin import bp as superadmin_bp
    app.register_blueprint(superadmin_bp)
except Exception as _sa_err:
    print(f"[app] Module superadmin non chargé : {_sa_err}")

# --- Garde tenant fail-closed (P0-R / M1) ---
@app.before_request
def _enforce_tenant_context():
    """Refuse l'accès à un utilisateur authentifié dépourvu d'école (hors superadmin)."""
    if request.endpoint == 'static':
        return
    if 'user_id' not in session:
        return  # routes publiques / utilisateur non authentifié
    if session.get('role') == 'superadmin':
        return  # le superadmin opère sur l'ensemble des écoles
    if not session.get('school_id'):
        session.clear()
        flash("Session invalide : aucune école associée. Veuillez vous reconnecter.", "danger")
        return redirect(url_for('login'))


# Ajouter le filtre strftime personnalisé
@app.template_filter('strftime')
def strftime_filter(date, format='%d/%m/%Y'):
    """Filtre personnalisé pour formater les dates"""
    if date is None:
        return 'N/A'

    if isinstance(date, str):
        try:
            # Essayer de parser la date si c'est une chaîne
            date = datetime.strptime(date, '%Y-%m-%d')
        except:
            return date  # Retourner la chaîne telle quelle si le parsing échoue

    if hasattr(date, 'strftime'):
        return date.strftime(format)
    else:
        return str(date)

# Ajouter le filtre pour formater les nombres
@app.template_filter('format_number')
def format_number_filter(number):
    """Filtre personnalisé pour formater les nombres"""
    try:
        return "{:,.0f}".format(float(number)).replace(",", " ")
    except:
        return str(number)

# Connexion DB MySQL (identifiants centralisés dans db.py via variables d'environnement)
from db import DB_CONFIG

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"Erreur de connexion à MySQL: {e}")
        return None

def is_admin(email):
    return email.lower().startswith("admin")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Merci de vous connecter.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'user_id' in session and 'role' in session:
        role = session['role']
        if role == 'admin':
            return redirect(url_for('admin_home'))
        elif role == 'etudiant':
            return redirect(url_for('student_dashboard'))
        elif role == 'professeur':
            return redirect(url_for('professeur_emploi_temps'))
    return redirect(url_for('register'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nom = request.form['nom']
        prenom = request.form['prenom']
        email = request.form['email']
        password = request.form['password']

        # Déduction du rôle par l'email
        if email.startswith('professeur'):
            role = 'professeur'
        elif email.startswith('admin'):
            role = 'admin'
        else:
            role = 'etudiant'

        # Filtrage conditionnel selon le rôle
        filiere = request.form.get('filiere') if role == 'etudiant' else None  
        niveau = request.form.get('niveau') if role == 'etudiant' else None

        hashed_password = generate_password_hash(password)

        # Route publique : l'école est résolue à partir d'une source fiable, jamais
        # via la session ni un défaut implicite. Priorité : école explicitement
        # choisie sur le formulaire (id/code/domaine) > domaine de l'email (démo)
        # > host. Échec explicite si rien de fiable (jamais de school_id=1).
        school_ref = request.form.get('school_id')
        school_id = tenant.resolve_public_school_id(
            explicit_school_ref=school_ref, email=email, host=request.host)
        if not school_id:
            flash("Établissement introuvable : sélectionnez votre école dans la "
                  "liste. Si elle n'apparaît pas, contactez votre administration.",
                  "danger")
            return redirect(url_for('register'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('register'))

        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO users (nom, prenom, email, password, role, filiere, niveau, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (nom, prenom, email, hashed_password, role, filiere, niveau, school_id))
            conn.commit()
        except mysql.connector.IntegrityError:
            flash("Cet email est déjà utilisé.")
            return redirect(url_for('register'))
        finally:
            conn.close()

        flash("Inscription réussie. Connectez-vous.")
        return redirect(url_for('login'))

    # GET : l'utilisateur choisit son établissement parmi les écoles actives ;
    # les filières affichées proviennent de « Filières & modules » de l'école choisie.
    schools = tenant.list_active_schools()
    return render_template('register.html', schools=schools)


@app.route('/api/public/filieres/<int:school_id>')
def api_public_filieres(school_id):
    """Filières actives d'une école (route PUBLIQUE, pour le formulaire d'inscription).
    Scopée strictement par school_id ; aucune fuite cross-école."""
    if not tenant.school_exists(school_id):
        return jsonify([])
    conn = get_db_connection()
    if not conn:
        return jsonify([])
    try:
        cursor = conn.cursor(dictionary=True)
        filieres = get_canonical_active_filieres(cursor, school_id)
    finally:
        conn.close()
    return jsonify([
        {'id': f['id'], 'nom': f['nom'], 'code': f.get('code'), 'niveau': f.get('niveau')}
        for f in filieres
    ])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['email'].lower().strip()
        password = request.form['password']

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return render_template('login.html', email=login_input)

        cursor = conn.cursor(dictionary=True)
        ensure_student_account_columns(cursor)
        conn.commit()

        if '@' in login_input:
            cursor.execute("SELECT * FROM users WHERE email = %s", (login_input,))
        else:
            cursor.execute("SELECT * FROM users WHERE identifiant = %s", (login_input.upper(),))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['nom'] = user['nom']
            session['prenom'] = user['prenom']
            session['user_email'] = user['email']
            session['filiere'] = user['filiere'] if user['filiere'] else ''
            session['niveau'] = user['niveau'] if user['niveau'] else ''
            _user_school_id = user.get('school_id')
            if not _user_school_id and user['role'] != 'superadmin':
                flash("Votre compte n'est rattaché à aucune école. "
                      "Contactez l'administrateur.", "danger")
                return render_template('login.html', email=login_input)
            session['school_id'] = _user_school_id
            if user.get('must_change_password'):
                session['must_change_password'] = True

            if user['role'] == 'superadmin':
                return redirect(url_for('superadmin.dashboard'))
            if user['role'] == 'admin':
                return redirect(url_for('admin_home'))
            elif user['role'] == 'professeur':
                return redirect(url_for('professeur_emploi_temps'))
            else:
                if user.get('must_change_password'):
                    flash("Bienvenue ! Veuillez changer votre mot de passe temporaire.", "info")
                    return redirect(url_for('student_profile') + '?change_password=1')
                return redirect(url_for('student_dashboard'))
        else:
            flash("Identifiants incorrects.", "danger")
            return render_template('login.html', email=login_input)

    return render_template('login.html')

# Route supprimée - utilisation de la route principale unifiée

# Route pour marquer les absences
@app.route('/professeur/marquer-absence', methods=['POST'])
@login_required
def marquer_absence():
    if session.get('role') != 'professeur':
        return jsonify({'error': 'Accès non autorisé'}), 403

    try:
        data = request.get_json()
        etudiant_id = data.get('etudiant_id')
        statut = data.get('statut')
        date_cours = data.get('date')

        if not all([etudiant_id, statut, date_cours]):
            return jsonify({'error': 'Données manquantes'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
        
        cursor = conn.cursor()
        year_id = get_current_year_id()

        # Insérer ou mettre à jour l'absence (MySQL utilise ON DUPLICATE KEY UPDATE)
        cursor.execute('''
        INSERT INTO presences
        (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, updated_at, annee_academique_id, school_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        statut = VALUES(statut),
        commentaire = VALUES(commentaire),
        updated_at = VALUES(updated_at)
        ''', (etudiant_id, 1, session['user_id'], date_cours, statut, '',
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'), year_id, tenant.current_school_id()))

        conn.commit()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for('login'))

# ----- Admin routes -----

@app.route('/admin/home')
@login_required
@admin_required
def admin_home():
    conn = get_db_connection()
    absence_stats = {'total_absences': 0, 'today_absences': 0, 'etudiants_concernes': 0}
    recent_absences = []
    unread_notifications = 0
    recent_credentials = []
    credentials_pending = 0

    if conn:
        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        year_filter = " AND p.annee_academique_id = %s" if year_id else ""
        params = (year_id,) if year_id else ()

        cursor.execute(f"""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT p.etudiant_id) as etudiants,
                   SUM(CASE WHEN p.date_cours = CURDATE() THEN 1 ELSE 0 END) as today_count
            FROM presences p
            WHERE p.statut IN ('absent', 'retard'){year_filter}
        """, params)
        row = cursor.fetchone() or {}
        absence_stats = {
            'total_absences': row.get('total') or 0,
            'today_absences': row.get('today_count') or 0,
            'etudiants_concernes': row.get('etudiants') or 0,
        }

        cursor.execute(f"""
            SELECT p.id, p.date_cours, p.statut, p.created_at,
                   u.prenom, u.nom, u.filiere, u.niveau,
                   c.nom_cours, prof.prenom as prof_prenom, prof.nom as prof_nom
            FROM presences p
            JOIN users u ON p.etudiant_id = u.id
            JOIN courses c ON p.course_id = c.id
            LEFT JOIN users prof ON p.professeur_id = prof.id
            WHERE p.statut IN ('absent', 'retard'){year_filter}
            ORDER BY p.created_at DESC
            LIMIT 8
        """, params)
        recent_absences = cursor.fetchall()

        try:
            cursor.execute("""
                SELECT id, prenom, nom, email, identifiant, password_temp, role,
                       filiere, niveau, classe
                FROM users
                WHERE password_temp IS NOT NULL AND password_temp != ''
                  AND must_change_password = 1
                  AND school_id = %s
                ORDER BY id DESC
                LIMIT 8
            """, (tenant.current_school_id(),))
            recent_credentials = cursor.fetchall()
            cursor.execute("""
                SELECT COUNT(*) AS c FROM users
                WHERE password_temp IS NOT NULL AND password_temp != ''
                  AND must_change_password = 1
                  AND school_id = %s
            """, (tenant.current_school_id(),))
            credentials_pending = (cursor.fetchone() or {}).get('c', 0)
        except Exception:
            pass

        try:
            ensure_notifications_table(cursor)
            cursor.execute(
                "SELECT COUNT(*) as c FROM notifications WHERE user_id = %s AND is_read = 0",
                (session.get('user_id'),)
            )
            unread_notifications = (cursor.fetchone() or {}).get('c', 0)
        except Exception:
            pass

        conn.close()

    return render_template(
        'admin_home.html',
        absence_stats=absence_stats,
        recent_absences=recent_absences,
        unread_notifications=unread_notifications,
        recent_credentials=recent_credentials,
        credentials_pending=credentials_pending,
    )

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    year_id = get_current_year_id()
    cursor = conn.cursor(dictionary=True)

    if year_id:
        cursor.execute("SELECT * FROM courses WHERE annee_academique_id = %s AND school_id = %s ORDER BY start", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT * FROM courses WHERE school_id = %s ORDER BY start", (tenant.current_school_id(),))

    courses = cursor.fetchall()
    conn.close()
    return render_template('admin_dashboard.html', courses=courses)

@app.route('/admin/add_course', methods=['GET', 'POST'])
@login_required
@admin_required
def add_course():
    if request.method == 'POST':
        nom_cours = request.form['nom_cours'].strip()
        professeur_id = request.form.get('professeur_id', '').strip()
        start = request.form['start']
        end = request.form['end']
        filiere = request.form['filiere'].strip()
        niveau = request.form.get('niveau', '').strip()  
        salle = request.form.get('salle', '').strip()
        description = request.form.get('description', '').strip()
        jour_semaine = request.form.get('jour_semaine', '').strip()
        heure_debut = request.form.get('heure_debut', '').strip()
        heure_fin = request.form.get('heure_fin', '').strip()
        recurrent = 1 if request.form.get('recurrent') == 'on' else 0

        if not nom_cours or not start or not end or not filiere:
            flash("Les champs nom du cours, date/heure de début, fin et filière sont obligatoires.", "danger")
            flash("Veuillez utiliser l'Interface Simple pour ajouter un cours.", "info")
            return redirect(url_for('admin_ajouter_cours_simple'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_dashboard'))

        cursor = conn.cursor(dictionary=True)

        # Récupérer les informations du professeur si spécifié
        professeur_nom = ''
        if professeur_id:
            cursor.execute("SELECT nom, prenom FROM users WHERE id = %s AND role = 'professeur'", (professeur_id,))
            prof = cursor.fetchone()
            if prof:
                professeur_nom = f"{prof['prenom']} {prof['nom']}"

        # Insérer le cours avec tous les champs, y compris niveau et année académique
        year_id = get_current_year_id()
        cursor.execute(
            """INSERT INTO courses (nom_cours, professeur_id, professeur_nom, start, end, filiere, niveau, salle, description, jour_semaine, heure_debut, heure_fin, recurrent, annee_academique_id, school_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (nom_cours, professeur_id if professeur_id else None, professeur_nom, start, end, filiere, niveau, salle, description, jour_semaine, heure_debut, heure_fin, recurrent, year_id, tenant.current_school_id())
        )

        course_id = cursor.lastrowid

        # 🚀 Inscription automatique (événement métier) : tous les étudiants de
        # la même école / filière / niveau sont inscrits à ce cours.
        nb_etudiants = sync_enrollments_for_course(cursor, course_id, tenant.current_school_id())

        # Ajouter le professeur à son emploi du temps s'il est spécifié
        if professeur_id:
            try:
                cursor.execute(
                    "INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications, school_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (professeur_id, course_id, 'professeur', 1, 1, tenant.current_school_id())
                )
            except Exception:
                pass  # Ignore les doublons

        conn.commit()
        conn.close()

        # Message de succès avec détails
        message = f"Cours '{nom_cours}' ajouté avec succès ! Automatiquement ajouté à l'emploi du temps de {nb_etudiants} étudiant(s)"
        if professeur_nom:
            message += f" et du professeur {professeur_nom}"
        message += "."

        flash(message, "success")
        return redirect(url_for('admin_dashboard'))

    # GET request - afficher le formulaire avec la liste des professeurs
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_dashboard'))
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, nom, prenom, specialite FROM users WHERE role = 'professeur' AND school_id = %s ORDER BY nom", (tenant.current_school_id(),))
    professeurs = cursor.fetchall()
    conn.close()
    # Rediriger vers l'interface simple d'ajout
    return redirect(url_for('admin_ajouter_cours_simple'))

@app.route('/admin/edit_course/<int:course_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_course(course_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_dashboard'))
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
    course = cursor.fetchone()

    if not course:
        conn.close()
        flash("Cours non trouvé.", "danger")
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        nom_cours = request.form['nom_cours'].strip()
        professeur = request.form.get('professeur', '').strip()
        start = request.form['start']
        end = request.form['end']
        filiere = request.form['filiere'].strip()

        if not nom_cours or not start or not end or not filiere:
            flash("Tous les champs sauf professeur sont obligatoires.", "danger")
            flash("Veuillez utiliser l'Interface Simple pour modifier un cours.", "info")
            return redirect(url_for('admin_dashboard'))

        cursor.execute(
            "UPDATE courses SET nom_cours=%s, professeur=%s, start=%s, end=%s, filiere=%s WHERE id=%s AND school_id=%s",
            (nom_cours, professeur, start, end, filiere, course_id, tenant.current_school_id())
        )

        # Événement métier : la filière du cours a pu changer. On recalcule les
        # inscriptions étudiantes (purge des lignes étudiant devenues hors
        # périmètre puis ré-inscription des étudiants correspondants, scopé école).
        # Les lignes 'professeur' sont préservées.
        try:
            cursor.execute(
                "DELETE FROM emploi_temps WHERE course_id = %s AND role = 'etudiant' AND school_id = %s",
                (course_id, tenant.current_school_id())
            )
            sync_enrollments_for_course(cursor, course_id, tenant.current_school_id())
        except Exception as e:
            print(f"⚠️ edit_course: resynchro emploi_temps ignorée ({e})")

        conn.commit()
        conn.close()
        flash("Cours modifié avec succès.", "success")
        return redirect(url_for('admin_dashboard'))

    conn.close()
    flash("La modification avancée n'est plus disponible. Utilisez l'Interface Simple.", "info")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_course/<int:course_id>')
@login_required
@admin_required
def delete_course(course_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_dashboard'))

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM courses WHERE id = %s AND school_id = %s",
                   (course_id, tenant.current_school_id()))
    course = cursor.fetchone()

    motif = request.args.get('motif', '').strip()

    if not course:
        conn.close()
        flash("Cours introuvable.", "danger")
        return redirect(url_for('admin_dashboard'))

    notify_course_cancellation(conn, course, motif=motif or "Annulation administrative")

    # Événement métier : retirer les inscriptions emploi_temps (scopé école).
    try:
        remove_course_enrollments(cursor, course_id, tenant.current_school_id())
    except Exception as e:
        print(f"⚠️ delete_course: nettoyage emploi_temps ignoré ({e})")

    # Supprimer les autres lignes filles qui pointent sur ce cours.
    # Certaines tables (documents, notes) ont une FK sans ON DELETE CASCADE :
    # on les nettoie explicitement, dans le bon ordre.
    dependent_tables = [
        'documents',
        'notes',
        'gradebook',
        'assignment_submissions',
        'assignments',
        'exams',
        'lectures',
        'presences',
    ]
    for table in dependent_tables:
        try:
            cursor.execute(f"DELETE FROM {table} WHERE course_id = %s", (course_id,))
        except Exception as e:
            # Table inexistante ou colonne absente : on log et on continue
            print(f"⚠️ delete_course: nettoyage {table} ignoré ({e})")

    try:
        cursor.execute("DELETE FROM courses WHERE id = %s AND school_id = %s",
                       (course_id, tenant.current_school_id()))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        flash(
            "Suppression impossible : des données liées à ce cours empêchent "
            f"sa suppression ({e}). Contactez l'administrateur technique.",
            "danger",
        )
        return redirect(url_for('admin_dashboard'))

    conn.close()
    flash("Cours annulé. Les étudiants et le professeur ont été notifiés par email/WhatsApp.", "success")
    return redirect(url_for('admin_dashboard'))

# ----- Student routes -----

@app.route('/student/dashboard')
@login_required
def student_dashboard():
    if session.get('role') != 'etudiant':
        flash("Accès refusé.", "danger")
        return redirect(url_for('admin_dashboard'))

    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('login'))

    # 🚀 RÉCUPÉRER LES COURS selon la filière et le niveau de l'étudiant
    user_filiere = session.get('filiere')
    user_niveau = session.get('niveau')
    cursor = conn.cursor(dictionary=True)

    # Vérifier si la colonne niveau existe dans courses
    # Récupérer tous les résultats pour éviter "Unread result found"
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    results = cursor.fetchall()
    niveau_exists = len(results) > 0

    # Matching tolérant (Intelligence Artificielle == IA, Master 2 == M2)
    f_aliases = filiere_aliases(cursor, user_filiere) or ([user_filiere] if user_filiere else [])
    n_aliases = niveau_aliases(user_niveau) if niveau_exists else []
    f_clause = (
        f"c.filiere IN ({','.join(['%s'] * len(f_aliases))})"
        if f_aliases else "1=1"
    )
    if n_aliases:
        n_clause = (
            f"(c.niveau IN ({','.join(['%s'] * len(n_aliases))}) "
            f"OR c.niveau IS NULL OR c.niveau = '')"
        )
    else:
        n_clause = ""

    _et_join = student_enrollment_join_sql('c', 'et')
    _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')

    if n_clause:
        courses_query = f"""
            SELECT c.* FROM courses c
            {_et_join}
            WHERE et.user_id = %s AND et.role = 'etudiant'
            AND {f_clause}
            AND {n_clause}
            {_tenant_w}
            ORDER BY c.start
        """
        cursor.execute(courses_query, (user_id, *f_aliases, *n_aliases, *_tenant_p))
    else:
        courses_query = f"""
            SELECT c.* FROM courses c
            {_et_join}
            WHERE et.user_id = %s AND et.role = 'etudiant'
            AND {f_clause}
            {_tenant_w}
            ORDER BY c.start
        """
        cursor.execute(courses_query, (user_id, *f_aliases, *_tenant_p))

    raw_courses = cursor.fetchall()

    events = []
    from datetime import datetime
    for course in raw_courses:
        course_dict = course

        # Titre enrichi avec salle et professeur
        title = course_dict['nom_cours']
        if course_dict.get('professeur_nom'):
            title += f" - {course_dict['professeur_nom']}"
        if course_dict.get('salle'):
            title += f" ({course_dict['salle']})"

        # Formatage des dates pour FullCalendar
        def format_date(dt):
            if isinstance(dt, str):
                try:
                    # Essaye de parser au format MySQL
                    return datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").isoformat()
                except Exception:
                    return dt
            elif isinstance(dt, datetime):
                return dt.isoformat()
            return str(dt)

        events.append({
            "title": title,
            "start": format_date(course_dict["start"]),
            "end": format_date(course_dict["end"]),
            "description": course_dict.get('description', ''),
            "salle": course_dict.get('salle', ''),
            "professeur": course_dict.get('professeur_nom', ''),
            "filiere": course_dict.get('filiere', ''),
            "niveau": course_dict.get('niveau', ''),
            "id": course_dict['id']
        })

    # Récupérer les documents récents pour les cours de l'étudiant
    # Utiliser nom_cours et filiere si disponibles, sinon utiliser course_id
    cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
    nom_cours_exists = cursor.fetchone() is not None
    
    if nom_cours_exists:
        cursor.execute('''
        SELECT DISTINCT d.id, d.titre, d.description, d.nom_fichier, d.date_upload,
               c.nom_cours, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN users u ON d.professeur_id = u.id
        JOIN courses c ON d.course_id = c.id AND d.school_id = c.school_id
        JOIN emploi_temps et ON c.id = et.course_id
            AND (et.school_id = c.school_id OR et.school_id IS NULL)
        WHERE et.user_id = %s AND et.role = 'etudiant' AND d.visible = 1
        AND c.school_id = %s AND d.school_id = %s
        AND d.date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
        ORDER BY d.date_upload DESC
        LIMIT 5
        ''', (session['user_id'], tenant.current_school_id(), tenant.current_school_id()))
    else:
        cursor.execute('''
        SELECT d.id, d.titre, d.description, d.nom_fichier, d.date_upload,
               c.nom_cours, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN courses c ON d.course_id = c.id AND d.school_id = c.school_id
        JOIN users u ON d.professeur_id = u.id
        JOIN emploi_temps et ON c.id = et.course_id
            AND (et.school_id = c.school_id OR et.school_id IS NULL)
        WHERE et.user_id = %s AND et.role = 'etudiant' AND d.visible = 1
        AND c.school_id = %s AND d.school_id = %s
        ORDER BY d.date_upload DESC
        LIMIT 5
        ''', (session['user_id'], tenant.current_school_id(), tenant.current_school_id()))
    documents_recents = cursor.fetchall()

    # Récupérer les absences récentes de l'étudiant
    year_id = get_current_year_id()
    if year_id:
        cursor.execute('''
        SELECT p.date_cours, p.statut, p.commentaire, c.nom_cours,
               u.nom as prof_nom, u.prenom as prof_prenom
        FROM presences p
        JOIN courses c ON p.course_id = c.id
        JOIN users u ON p.professeur_id = u.id
        WHERE p.etudiant_id = %s AND p.statut IN ('absent', 'retard') AND p.annee_academique_id = %s
        ORDER BY p.date_cours DESC
        LIMIT 5
        ''', (session['user_id'], year_id))
    else:
        cursor.execute('''
        SELECT p.date_cours, p.statut, p.commentaire, c.nom_cours,
               u.nom as prof_nom, u.prenom as prof_prenom
        FROM presences p
        JOIN courses c ON p.course_id = c.id
        JOIN users u ON p.professeur_id = u.id
        WHERE p.etudiant_id = %s AND p.statut IN ('absent', 'retard')
        ORDER BY p.date_cours DESC
        LIMIT 5
        ''', (session['user_id'],))
    absences_recentes = cursor.fetchall()

    in_app_notifications = []
    try:
        ensure_notifications_table(cursor)
        cursor.execute("""
            SELECT id, type, title, message, link, created_at
            FROM notifications
            WHERE user_id = %s AND is_read = 0
            ORDER BY created_at DESC
            LIMIT 10
        """, (session['user_id'],))
        in_app_notifications = cursor.fetchall()
    except Exception:
        pass

    # Calculer les vraies statistiques
    from datetime import datetime, timedelta

    # Créer un nouveau curseur pour les statistiques pour éviter "Unread result found"
    stats_cursor = conn.cursor(dictionary=True)

    # 1. Cours cette semaine (utiliser start ou date_cours et gérer les récurrences par jour)
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())  # Lundi de cette semaine
    end_of_week = start_of_week + timedelta(days=6)  # Dimanche de cette semaine
    start_of_week_str = start_of_week.strftime('%Y-%m-%d')
    end_of_week_str = end_of_week.strftime('%Y-%m-%d')
    # Jours de la semaine (Fr) pour correspondre à c.jour_semaine
    jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

    # Compter les cours cette semaine (une occurrence par cours si planifié dans la semaine)
    # Filtres tolérants partagés (IA == Intelligence Artificielle, M2 == Master 2)
    stats_f_aliases = filiere_aliases(stats_cursor, user_filiere) or (
        [user_filiere] if user_filiere else []
    )
    stats_n_aliases = niveau_aliases(user_niveau) if niveau_exists else []
    filiere_filter = (
        f"AND c.filiere IN ({','.join(['%s'] * len(stats_f_aliases))})"
        if stats_f_aliases else ""
    )
    if stats_n_aliases:
        niveau_filter = (
            f"AND (c.niveau IN ({','.join(['%s'] * len(stats_n_aliases))}) "
            f"OR c.niveau IS NULL OR c.niveau = '')"
        )
    else:
        niveau_filter = ""

    base_filters_params = (*stats_f_aliases, *stats_n_aliases)
    _stats_tenant_w, _stats_tenant_p = student_course_tenant_where('c', 'et')
    params_cours = (user_id, *base_filters_params, *_stats_tenant_p,
                    start_of_week_str, end_of_week_str,
                    start_of_week_str, end_of_week_str,
                    jours_semaine[0], jours_semaine[1], jours_semaine[2],
                    jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])

    query_cours_semaine = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        {student_enrollment_join_sql('c', 'et')}
        WHERE et.user_id = %s AND et.role = 'etudiant'
        {filiere_filter}
        {niveau_filter}
        {_stats_tenant_w}
        AND (
            (c.date_cours IS NOT NULL AND DATE(c.date_cours) BETWEEN %s AND %s)
            OR (c.date_cours IS NULL AND DATE(c.start) BETWEEN %s AND %s)
            OR (c.recurrent = 1 AND c.jour_semaine IN (%s, %s, %s, %s, %s, %s, %s))
        )
    '''
    stats_cursor.execute(query_cours_semaine, params_cours)
    result = stats_cursor.fetchall()
    cours_cette_semaine = result[0] if result else {'count': 0}

    # Cours semaine dernière pour comparaison
    start_last_week = start_of_week - timedelta(days=7)
    end_last_week = start_of_week - timedelta(days=1)
    start_last_week_str = start_last_week.strftime('%Y-%m-%d')
    end_last_week_str = end_last_week.strftime('%Y-%m-%d')

    params_cours_last = (user_id, *base_filters_params, *_stats_tenant_p,
                         start_last_week_str, end_last_week_str,
                         start_last_week_str, end_last_week_str,
                         jours_semaine[0], jours_semaine[1], jours_semaine[2],
                         jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])

    query_cours_semaine_last = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        {student_enrollment_join_sql('c', 'et')}
        WHERE et.user_id = %s AND et.role = 'etudiant'
        {filiere_filter}
        {niveau_filter}
        {_stats_tenant_w}
        AND (
            (c.date_cours IS NOT NULL AND DATE(c.date_cours) BETWEEN %s AND %s)
            OR (c.date_cours IS NULL AND DATE(c.start) BETWEEN %s AND %s)
            OR (c.recurrent = 1 AND c.jour_semaine IN (%s, %s, %s, %s, %s, %s, %s))
        )
    '''
    stats_cursor.execute(query_cours_semaine_last, params_cours_last)
    result = stats_cursor.fetchall()
    cours_semaine_derniere = result[0] if result else {'count': 0}

    # 2. Absences (toutes absences/retards enregistrées dans "Mes absences")
    year_id = get_current_year_id()
    if year_id:
        stats_cursor.execute('''
            SELECT COUNT(*) as count FROM presences p
            WHERE p.etudiant_id = %s
            AND p.statut IN ('absent', 'retard')
            AND p.annee_academique_id = %s
        ''', (user_id, year_id))
    else:
        stats_cursor.execute('''
            SELECT COUNT(*) as count FROM presences p
            WHERE p.etudiant_id = %s
            AND p.statut IN ('absent', 'retard')
        ''', (user_id,))
    result = stats_cursor.fetchall()
    absences_total = result[0] if result else {'count': 0}

    # 3. Prochains examens (cours avec "examen" dans le nom ou description, ou date future)
    today_str = today.strftime('%Y-%m-%d')
    params_examens = (user_id, *base_filters_params, *_stats_tenant_p, today_str, today_str)

    query_examens = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        {student_enrollment_join_sql('c', 'et')}
        WHERE et.user_id = %s AND et.role = 'etudiant'
        {filiere_filter}
        {niveau_filter}
        {_stats_tenant_w}
        AND (
            LOWER(c.nom_cours) LIKE '%%examen%%'
            OR LOWER(c.nom_cours) LIKE '%%test%%'
            OR LOWER(c.nom_cours) LIKE '%%evaluation%%'
            OR LOWER(c.description) LIKE '%%examen%%'
            OR LOWER(c.description) LIKE '%%test%%'
            OR LOWER(c.description) LIKE '%%evaluation%%'
        )
        AND (
            (c.date_cours IS NOT NULL AND DATE(c.date_cours) >= %s)
            OR (c.date_cours IS NULL AND DATE(c.start) >= %s)
        )
    '''
    stats_cursor.execute(query_examens, params_examens)
    result = stats_cursor.fetchall()
    prochains_examens = result[0] if result else {'count': 0}

    # Prochain examen le plus proche
    query_examen_date = f'''
        SELECT MIN(COALESCE(DATE(c.date_cours), DATE(c.start))) as next_exam
        FROM courses c
        {student_enrollment_join_sql('c', 'et')}
        WHERE et.user_id = %s AND et.role = 'etudiant'
        {filiere_filter}
        {niveau_filter}
        {_stats_tenant_w}
        AND (
            LOWER(c.nom_cours) LIKE '%%examen%%'
            OR LOWER(c.nom_cours) LIKE '%%test%%'
            OR LOWER(c.nom_cours) LIKE '%%evaluation%%'
            OR LOWER(c.description) LIKE '%%examen%%'
            OR LOWER(c.description) LIKE '%%test%%'
            OR LOWER(c.description) LIKE '%%evaluation%%'
        )
        AND (
            (c.date_cours IS NOT NULL AND DATE(c.date_cours) >= %s)
            OR (c.date_cours IS NULL AND DATE(c.start) >= %s)
        )
    '''
    stats_cursor.execute(query_examen_date, params_examens)
    result = stats_cursor.fetchall()
    prochain_examen_date = result[0] if result else {'next_exam': None}

    # Calculer les jours jusqu'au prochain examen
    jours_prochain_examen = 0
    if prochain_examen_date and prochain_examen_date['next_exam']:
        try:
            if isinstance(prochain_examen_date['next_exam'], str):
                exam_date = datetime.strptime(prochain_examen_date['next_exam'], '%Y-%m-%d')
            else:
                exam_date = prochain_examen_date['next_exam']
            jours_prochain_examen = (exam_date.date() - today.date()).days
            if jours_prochain_examen < 0:
                jours_prochain_examen = 0
        except:
            jours_prochain_examen = 0

    # 4. Moyenne générale (basée sur la table notes)
    year_id = get_current_year_id()
    if year_id:
        stats_cursor.execute('''
            SELECT CC1, CC2, Participation, Examen
            FROM notes
            WHERE etudiant_id = %s AND annee_academique_id = %s
            AND (CC1 IS NOT NULL OR CC2 IS NOT NULL OR Participation IS NOT NULL OR Examen IS NOT NULL)
        ''', (user_id, year_id))
    else:
        stats_cursor.execute('''
            SELECT CC1, CC2, Participation, Examen
            FROM notes
            WHERE etudiant_id = %s
            AND (CC1 IS NOT NULL OR CC2 IS NOT NULL OR Participation IS NOT NULL OR Examen IS NOT NULL)
        ''', (user_id,))
    notes_rows = stats_cursor.fetchall()
    
    moyenne_generale = 0.0
    moyenne_mois_dernier = 0.0
    
    if notes_rows:
        # Calculer la moyenne actuelle
        total_moyennes = 0
        nb_cours_avec_notes = 0
        
        for note in notes_rows:
            # Calculer la moyenne pour ce cours
            notes_valides = []
            if note.get('CC1') is not None:
                try:
                    notes_valides.append(float(note['CC1']))
                except Exception:
                    pass
            if note.get('CC2') is not None:
                try:
                    notes_valides.append(float(note['CC2']))
                except Exception:
                    pass
            if note.get('Participation') is not None:
                try:
                    notes_valides.append(float(note['Participation']))
                except Exception:
                    pass
            if note.get('Examen') is not None:
                try:
                    notes_valides.append(float(note['Examen']))
                except Exception:
                    pass
            
            if notes_valides:
                moyenne_cours = sum(notes_valides) / len(notes_valides)
                total_moyennes += moyenne_cours
                nb_cours_avec_notes += 1
        
        if nb_cours_avec_notes > 0:
            moyenne_generale = float(total_moyennes) / float(nb_cours_avec_notes)
        
        # Calculer la variation de moyenne (basée sur l'évolution récente)
        # Pour une vraie comparaison temporelle, il faudrait stocker l'historique
        # Ici, on calcule une variation basée sur les performances récentes
        total_absences = absences_total['count'] if absences_total else 0
        # Variation basée sur les absences (plus d'absences = baisse potentielle)
        # Mais aussi sur la moyenne actuelle (si moyenne > 15, tendance positive)
        if float(moyenne_generale) >= 15.0:
            variation_moyenne = max(0.0, min(2.0, (float(moyenne_generale) - 15.0) * 0.1 - float(total_absences) * 0.05))
        else:
            variation_moyenne = max(-2.0, min(0.0, (float(moyenne_generale) - 15.0) * 0.1 - float(total_absences) * 0.1))
    else:
        # Pas de notes, moyenne par défaut basée sur les absences
        total_absences = absences_total['count'] if absences_total else 0
        moyenne_generale = max(10.0, 20.0 - (float(total_absences) * 1.5))
        variation_moyenne = 0.0

    # Statistiques pour le template
    stats = {
        'cours_cette_semaine': cours_cette_semaine['count'] if cours_cette_semaine else 0,
        'cours_semaine_derniere': cours_semaine_derniere['count'] if cours_semaine_derniere else 0,
        'moyenne_generale': round(moyenne_generale, 1),
        'absences_a_justifier': absences_total['count'] if absences_total else 0,
        'prochains_examens': prochains_examens['count'] if prochains_examens else 0,
        'jours_prochain_examen': jours_prochain_examen
    }

    # Calcul des variations
    stats['variation_cours'] = stats['cours_cette_semaine'] - stats['cours_semaine_derniere']
    stats['variation_moyenne'] = round(variation_moyenne, 1)

    cursor.execute(
        "SELECT identifiant, classe, filiere, niveau, email FROM users WHERE id = %s",
        (user_id,),
    )
    student_info = cursor.fetchone() or {}

    # Fermer le curseur des statistiques avant de fermer la connexion
    stats_cursor.close()
    conn.close()

    return render_template('student_dashboard.html',
                           events=events,
                           nom=session.get('nom', ''),
                           prenom=session.get('prenom', ''),
                           student_info=student_info,
                           documents_recents=documents_recents,
                           absences_recentes=absences_recentes,
                           in_app_notifications=in_app_notifications,
                           stats=stats)

# 🎯 EMPLOI DU TEMPS PERSONNALISÉ POUR CHAQUE PROFESSEUR (ULTRA-SÉCURISÉ)
@app.route('/professeur/emploi-temps')
@login_required
def professeur_emploi_temps():
    try:
        if session.get('role') != 'professeur':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('login'))

        from datetime import datetime, timedelta

        # 🎯 RÉCUPÉRER UNIQUEMENT LES COURS DE CE PROFESSEUR (SÉCURISÉ)
        try:
            cursor = conn.cursor(dictionary=True)
            courses_query = """
                SELECT c.*, et.visible, et.notifications, et.created_at
                FROM courses c
                JOIN emploi_temps et ON c.id = et.course_id
                WHERE et.user_id = %s AND et.role = 'professeur'
                ORDER BY c.jour_semaine, c.heure_debut
            """
            cursor.execute(courses_query, (user_id,))
            courses = cursor.fetchall()
            courses = list(courses) if courses else []
        except Exception as e:
            print(f"Erreur récupération cours planning: {e}")
            courses = []

        # Organiser par jour de la semaine UNIQUEMENT POUR CE PROFESSEUR
        jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
        emploi_temps = {jour: [] for jour in jours_semaine}

        # 🎯 Préparer les événements pour FullCalendar (comme pour étudiant)
        events = []
        total_etudiants = 0

        for course in courses:
            try:
                course_dict = course

                if course_dict.get('jour_semaine') and course_dict['jour_semaine'] in emploi_temps:
                    # Compter les étudiants pour ce cours
                    try:
                        cursor.execute(
                            "SELECT COUNT(*) as count FROM users WHERE role='etudiant' AND filiere = %s AND school_id = %s",
                            (course_dict['filiere'], tenant.current_school_id())
                        )
                        nb_etudiants_result = cursor.fetchone()
                        nb_etudiants = nb_etudiants_result['count'] if nb_etudiants_result else 0
                        total_etudiants += nb_etudiants
                    except Exception as e:
                        print(f"Erreur calcul étudiants: {e}")
                        nb_etudiants = 0

                    course_dict['nb_etudiants'] = nb_etudiants
                    emploi_temps[course_dict['jour_semaine']].append(course_dict)

                # Formater pour FullCalendar
                def format_date(dt):
                    if isinstance(dt, str):
                        try:
                            return datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").isoformat()
                        except Exception:
                            return dt
                    elif isinstance(dt, datetime):
                        return dt.isoformat()
                    return str(dt) if dt else None

                # Titre enrichi avec filière et salle
                title = course_dict['nom_cours']
                if course_dict.get('salle'):
                    title += f" ({course_dict['salle']})"

                events.append({
                    "id": course_dict['id'],
                    "title": title,
                    "start": format_date(course_dict.get("start")),
                    "end": format_date(course_dict.get("end")),
                    "description": course_dict.get('description', ''),
                    "salle": course_dict.get('salle', ''),
                    "filiere": course_dict.get('filiere', ''),
                    "niveau": course_dict.get('niveau', ''),
                    "nb_etudiants": course_dict.get('nb_etudiants', 0)
                })
            except Exception as e:
                print(f"Erreur traitement cours planning: {e}")
                continue

        # 🎯 Calculer les statistiques pour le professeur
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())

        # Cours cette semaine
        cours_cette_semaine = len(courses)

        # Nombre total d'étudiants (unique par filière)
        try:
            cursor.execute("""
                SELECT COUNT(DISTINCT u.id) as count
                FROM users u
                WHERE u.role = 'etudiant' AND u.school_id = %s
                AND u.filiere IN (
                    SELECT DISTINCT c.filiere FROM courses c
                    JOIN emploi_temps et ON c.id = et.course_id
                    WHERE et.user_id = %s AND et.role = 'professeur'
                )
            """, (tenant.current_school_id(), user_id))
            result = cursor.fetchone()
            total_etudiants = result['count'] if result else 0
        except Exception as e:
            print(f"Erreur calcul total étudiants: {e}")
            total_etudiants = 0

        # Présences à valider (présences non marquées)
        try:
            year_id = get_current_year_id()
            if year_id:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM courses c
                    JOIN emploi_temps et ON c.id = et.course_id
                    WHERE et.user_id = %s AND et.role = 'professeur'
                    AND c.id NOT IN (
                        SELECT DISTINCT course_id FROM presences
                        WHERE DATE(date_cours) = CURDATE() AND annee_academique_id = %s
                    )
                """, (user_id, year_id))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM courses c
                    JOIN emploi_temps et ON c.id = et.course_id
                    WHERE et.user_id = %s AND et.role = 'professeur'
                    AND c.id NOT IN (
                        SELECT DISTINCT course_id FROM presences
                        WHERE DATE(date_cours) = CURDATE()
                    )
                """, (user_id,))
            result = cursor.fetchone()
            presences_a_valider = result['count'] if result else 0
        except Exception as e:
            print(f"Erreur calcul présences: {e}")
            presences_a_valider = 0

        # Notes à saisir (estimation basée sur les cours sans notes récentes)
        notes_a_saisir = 0

        stats = {
            'cours_cette_semaine': cours_cette_semaine,
            'total_etudiants': total_etudiants,
            'presences_a_valider': presences_a_valider,
            'notes_a_saisir': notes_a_saisir
        }

        cancel_notifications = []
        try:
            ensure_notifications_table(cursor)
            cursor.execute("""
                SELECT id, title, message, created_at FROM notifications
                WHERE user_id = %s AND type = 'course_cancelled' AND is_read = 0
                ORDER BY created_at DESC LIMIT 5
            """, (user_id,))
            cancel_notifications = cursor.fetchall()
        except Exception:
            pass

        conn.close()

        return render_template('professeur_dashboard.html',
                               events=events,
                               emploi_temps=emploi_temps,
                               jours_semaine=jours_semaine,
                               stats=stats,
                               cancel_notifications=cancel_notifications,
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''))

    except Exception as e:
        # 🚨 GESTION D'ERREUR GLOBALE POUR LE PLANNING
        import traceback
        error_msg = f"Erreur planning professeur: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)

        # Retourner un planning vide en cas d'erreur
        jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
        emploi_temps_vide = {jour: [] for jour in jours_semaine}
        stats_vide = {
            'cours_cette_semaine': 0,
            'total_etudiants': 0,
            'presences_a_valider': 0,
            'notes_a_saisir': 0
        }

        return render_template('professeur_dashboard.html',
                               events=[],
                               emploi_temps=emploi_temps_vide,
                               jours_semaine=jours_semaine,
                               stats=stats_vide,
                               cancel_notifications=[],
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''),
                               error_message="Erreur lors du chargement du planning. Veuillez réessayer.")

# 🎯 GESTION COMPLÈTE DU MODULE PAR LE PROFESSEUR

def _professor_owns_course(cursor, course_id, user_id):
    """Vérifie que le professeur est assigné au cours et retourne le cours."""
    cursor.execute("""
        SELECT c.*
        FROM courses c
        LEFT JOIN emploi_temps et ON c.id = et.course_id AND et.user_id = %s AND et.role = 'professeur'
        WHERE c.id = %s AND (c.professeur_id = %s OR et.user_id IS NOT NULL)
    """, (user_id, course_id, user_id))
    return cursor.fetchone()


def get_course_class_students(cursor, course_id, course):
    """
    Étudiants de la classe du cours : correspondance stricte filière + niveau.
    Sans niveau sur le cours : inscrits via emploi_temps pour ce cours uniquement.
    """
    filiere = (course.get('filiere') or '').strip()
    niveau = (course.get('niveau') or '').strip()

    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    classe_exists = cursor.fetchone() is not None
    classe_col = ", u.classe" if classe_exists else ", NULL as classe"
    cols = f"u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau{classe_col}"

    if filiere and niveau:
        cursor.execute(f"""
            SELECT {cols} FROM users u
            WHERE u.role = 'etudiant' AND u.filiere = %s AND u.niveau = %s AND u.school_id = %s
            ORDER BY u.nom, u.prenom
        """, (filiere, niveau, tenant.current_school_id()))
        return cursor.fetchall()

    if not filiere:
        return []

    cursor.execute(f"""
        SELECT DISTINCT {cols}
        FROM users u
        INNER JOIN emploi_temps et ON et.user_id = u.id AND et.course_id = %s AND et.role = 'etudiant'
        WHERE u.role = 'etudiant' AND u.filiere = %s AND u.school_id = %s
        ORDER BY u.nom, u.prenom
    """, (course_id, filiere, tenant.current_school_id()))
    return cursor.fetchall()


def ensure_notifications_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            type VARCHAR(50) NOT NULL,
            title VARCHAR(255) NOT NULL,
            message TEXT,
            link VARCHAR(500),
            is_read TINYINT(1) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_notif_user (user_id, is_read)
        )
    """)


def create_in_app_notification(cursor, user_id, ntype, title, message, link=''):
    ensure_notifications_table(cursor)
    cursor.execute("""
        INSERT INTO notifications (user_id, type, title, message, link, school_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, ntype, title, message, link, tenant.current_school_id()))


def _format_presence_date_fr(date_str):
    try:
        return datetime.strptime(str(date_str)[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return str(date_str)


def process_absence_notifications(conn, course_id, date_cours, presences_list, professeur_id, previous_statuts=None):
    """Notifications in-app + email/WhatsApp quand une absence/retard est enregistré."""
    if not presences_list or not notify_absence_marked:
        return

    previous_statuts = previous_statuts or {}

    cursor = conn.cursor(dictionary=True)
    course = _professor_owns_course(cursor, course_id, professeur_id)
    if not course:
        cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
        course = cursor.fetchone()
    if not course:
        cursor.close()
        return

    cursor.execute("SELECT prenom, nom FROM users WHERE id = %s", (professeur_id,))
    prof = cursor.fetchone() or {}
    prof_name = f"{prof.get('prenom', '')} {prof.get('nom', '')}".strip()

    cursor.execute("SELECT id, prenom, nom, email, telephone FROM users WHERE role = 'admin' AND school_id = %s", (tenant.current_school_id(),))
    admins = cursor.fetchall()

    absence_items = []
    if isinstance(presences_list, dict):
        for eid, info in presences_list.items():
            absence_items.append({
                'etudiant_id': int(eid),
                'statut': info.get('statut', 'unspecified'),
            })
    else:
        for p in presences_list:
            absence_items.append({
                'etudiant_id': int(p.get('etudiant_id') or p.get('etudiantId')),
                'statut': p.get('statut', 'unspecified'),
            })

    year_id = get_current_year_id()
    for item in absence_items:
        statut = item['statut']
        if statut not in ('absent', 'retard'):
            continue
        etudiant_id = item['etudiant_id']

        prev_statut = previous_statuts.get(etudiant_id)
        if prev_statut == statut:
            continue
        if prev_statut in ('absent', 'retard') and statut in ('absent', 'retard'):
            continue

        cursor.execute(
            "SELECT id, prenom, nom, email, telephone, filiere, niveau FROM users WHERE id = %s",
            (etudiant_id,)
        )
        student = cursor.fetchone()
        if not student:
            continue

        label = 'Absence' if statut == 'absent' else 'Retard'
        date_fr = _format_presence_date_fr(date_cours)
        title = f"{label} — {course.get('nom_cours', 'Cours')}"
        msg = f"{label} enregistré le {date_fr} pour {course.get('nom_cours', '')}."
        create_in_app_notification(
            cursor, etudiant_id, 'absence', title, msg, '/student/absences'
        )
        for admin in admins:
            create_in_app_notification(
                cursor, admin['id'], 'absence_alert',
                f"Nouvelle {label.lower()}",
                f"{student['prenom']} {student['nom']} — {course.get('nom_cours', '')} ({date_fr})",
                '/admin/absences'
            )

        try:
            notify_absence_marked(student, course, date_cours, statut, prof_name)
            for admin in admins:
                notify_admin_absence_alert(admin, student, course, date_cours, statut)
        except Exception as e:
            print(f"Erreur notification absence: {e}")

    conn.commit()
    cursor.close()


def notify_course_cancellation(conn, course, motif=''):
    """Notifier prof + étudiants concernés de l'annulation d'un cours."""
    if not course or not notify_course_cancelled:
        return

    cursor = conn.cursor(dictionary=True)
    course_id = course['id']

    students = get_course_class_students(cursor, course_id, course)
    prof_ids = set()

    if course.get('professeur_id'):
        prof_ids.add(course['professeur_id'])
    cursor.execute("""
        SELECT user_id FROM emploi_temps
        WHERE course_id = %s AND role = 'professeur'
    """, (course_id,))
    for row in cursor.fetchall():
        prof_ids.add(row['user_id'])

    for pid in prof_ids:
        cursor.execute(
            "SELECT id, prenom, nom, email, telephone FROM users WHERE id = %s",
            (pid,)
        )
        prof = cursor.fetchone()
        if prof:
            create_in_app_notification(
                cursor, prof['id'], 'course_cancelled',
                f"Cours annulé — {course.get('nom_cours', '')}",
                f"Le cours a été retiré de l'emploi du temps.{(' Motif : ' + motif) if motif else ''}",
                '/professeur/emploi-temps'
            )
            try:
                notify_course_cancelled(prof, course, role='professeur', motif=motif)
            except Exception as e:
                print(f"Erreur notif prof annulation: {e}")

    for student in students:
        create_in_app_notification(
            cursor, student['id'], 'course_cancelled',
            f"Cours annulé — {course.get('nom_cours', '')}",
            f"Ce cours a été retiré de votre emploi du temps.{(' Motif : ' + motif) if motif else ''}",
            '/student/dashboard'
        )
        try:
            notify_course_cancelled(student, course, role='etudiant', motif=motif)
        except Exception as e:
            print(f"Erreur notif étudiant annulation: {e}")

    conn.commit()
    cursor.close()


@app.route('/professeur/course/<int:course_id>/manage')
@login_required
def professeur_course_manage(course_id):
    """Page de gestion complète d'un module avec 7 onglets"""
    try:
        if session.get('role') != 'professeur':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('professeur_emploi_temps'))

        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que le cours appartient à ce professeur
        cursor.execute("""
            SELECT c.*, u.prenom, u.nom
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            JOIN users u ON et.user_id = u.id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
        """, (course_id, user_id))
        
        course = cursor.fetchone()
        
        if not course:
            print(f"DEBUG: Cours non trouvé - Course ID: {course_id}, User ID: {user_id}")
            # Vérifier si le cours existe
            cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
            course_exists = cursor.fetchone()
            if course_exists:
                print(f"DEBUG: Le cours existe mais n'est pas associé à ce professeur")
                cursor.execute("""
                    SELECT et.* FROM emploi_temps et 
                    WHERE et.course_id = %s AND et.role = 'professeur' AND et.school_id = %s
                """, (course_id, tenant.current_school_id()))
                emploi_temps_data = cursor.fetchall()
                print(f"DEBUG: Emploi temps pour ce cours: {emploi_temps_data}")
            flash("Cours non trouvé ou accès refusé. Vérifiez que vous êtes bien assigné à ce cours.", "danger")
            conn.close()
            return redirect(url_for('professeur_emploi_temps'))

        # Récupérer les étudiants de la classe (filière + niveau stricts)
        etudiants = get_course_class_students(cursor, course_id, course)

        # Récupérer les présences pour ce cours
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        year_id = get_current_year_id()
        if year_id:
            cursor.execute("""
                SELECT etudiant_id, date_cours, statut, commentaire
                FROM presences
                WHERE course_id = %s AND annee_academique_id = %s
                ORDER BY date_cours DESC, etudiant_id
            """, (course_id, year_id))
        else:
            cursor.execute("""
                SELECT etudiant_id, date_cours, statut, commentaire
                FROM presences
                WHERE course_id = %s
                ORDER BY date_cours DESC, etudiant_id
            """, (course_id,))
        presences = {f"{p['etudiant_id']}_{p['date_cours']}": p for p in cursor.fetchall()}

        # Récupérer les documents/ressources pour ce module (par nom_cours et filiere)
        # Les documents restent disponibles pendant 5 ans
        nom_cours = course['nom_cours']
        filiere_cours = course['filiere']
        
        # Vérifier si les colonnes nom_cours et filiere existent dans documents
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
        nom_cours_exists = cursor.fetchone() is not None
        
        if nom_cours_exists:
            # Récupérer tous les documents de ce module (nom_cours + filiere) de moins de 5 ans
            cursor.execute("""
                SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload, visible
                FROM documents
                WHERE nom_cours = %s AND filiere = %s AND school_id = %s
                AND date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
                ORDER BY date_upload DESC
            """, (nom_cours, filiere_cours, tenant.current_school_id()))
        else:
            # Si les colonnes n'existent pas encore, utiliser course_id (compatibilité)
            cursor.execute("""
                SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload, visible
                FROM documents
                WHERE course_id = %s AND school_id = %s
                ORDER BY date_upload DESC
            """, (course_id, tenant.current_school_id()))
        documents = cursor.fetchall()

        # Récupérer les notes (si la table existe)
        notes_data = {}
        try:
            cursor.execute("""
                SELECT etudiant_id, nom_cours, CC1, CC2, Participation, Examen
                FROM notes
                WHERE nom_cours = %s AND school_id = %s
            """, (course['nom_cours'], tenant.current_school_id()))
            for note in cursor.fetchall():
                notes_data[note['etudiant_id']] = note
        except:
            pass  # Table notes peut ne pas exister

        # Vérifier et créer les tables nécessaires si elles n'existent pas
        try:
            # Table lectures (contenus de cours)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lectures (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    course_id INT NOT NULL,
                    titre VARCHAR(255) NOT NULL,
                    description TEXT,
                    contenu TEXT,
                    date_seance DATE,
                    ordre INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
            """)
            
            # Table exams
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exams (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    course_id INT NOT NULL,
                    type_examen VARCHAR(50) NOT NULL,
                    titre VARCHAR(255) NOT NULL,
                    date_examen DATE,
                    coefficient DOUBLE DEFAULT 1.0,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
            """)
            
            # Table assignments
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS assignments (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    course_id INT NOT NULL,
                    titre VARCHAR(255) NOT NULL,
                    description TEXT,
                    date_publication DATE,
                    date_limite DATE,
                    type_assignment VARCHAR(50) DEFAULT 'devoir',
                    fichier_corrige VARCHAR(255),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
            """)
            
            # Table assignment_submissions (devoirs rendus)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS assignment_submissions (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    assignment_id INT NOT NULL,
                    etudiant_id INT NOT NULL,
                    fichier_rendu VARCHAR(255),
                    date_rendu DATETIME DEFAULT CURRENT_TIMESTAMP,
                    note DOUBLE,
                    commentaire TEXT,
                    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
                    FOREIGN KEY (etudiant_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(assignment_id, etudiant_id)
                )
            """)
            
            # Table gradebook (notes détaillées)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gradebook (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    course_id INT NOT NULL,
                    etudiant_id INT NOT NULL,
                    professeur_id INT NOT NULL,
                    type_note VARCHAR(50) NOT NULL,
                    note DOUBLE NOT NULL,
                    coefficient DOUBLE DEFAULT 1.0,
                    date_note DATE,
                    commentaire TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
                    FOREIGN KEY (etudiant_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (professeur_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            
            # Ajouter la colonne professeur_id si elle n'existe pas (pour les tables existantes)
            try:
                cursor.execute("SHOW COLUMNS FROM gradebook LIKE 'professeur_id'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE gradebook ADD COLUMN professeur_id INT NOT NULL AFTER etudiant_id")
                    cursor.execute("ALTER TABLE gradebook ADD FOREIGN KEY (professeur_id) REFERENCES users(id) ON DELETE CASCADE")
            except Exception as e:
                print(f"Note: colonne professeur_id peut déjà exister: {e}")

            # Ajouter la colonne annee_academique_id si elle n'existe pas
            try:
                cursor.execute("SHOW COLUMNS FROM gradebook LIKE 'annee_academique_id'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE gradebook ADD COLUMN annee_academique_id INT")
            except Exception as e:
                print(f"Note: colonne annee_academique_id peut déjà exister: {e}")

            conn.commit()
        except Exception as e:
            print(f"Erreur création tables: {e}")
            pass

        # Récupérer les lectures
        cursor.execute("""
            SELECT id, titre, description, contenu, date_seance, ordre
            FROM lectures
            WHERE course_id = %s AND school_id = %s
            ORDER BY ordre, date_seance, created_at
        """, (course_id, tenant.current_school_id()))
        lectures = cursor.fetchall()

        # Récupérer les exams
        cursor.execute("""
            SELECT id, type_examen, titre, date_examen, coefficient, description
            FROM exams
            WHERE course_id = %s AND school_id = %s
            ORDER BY date_examen DESC
        """, (course_id, tenant.current_school_id()))
        exams = cursor.fetchall()

        # Récupérer les assignments
        cursor.execute("""
            SELECT id, titre, description, date_publication, date_limite, type_assignment, fichier_corrige
            FROM assignments
            WHERE course_id = %s AND school_id = %s
            ORDER BY date_limite DESC
        """, (course_id, tenant.current_school_id()))
        assignments = cursor.fetchall()

        # Récupérer les notes du gradebook
        cursor.execute("""
            SELECT etudiant_id, type_note, note, coefficient, date_note, commentaire
            FROM gradebook
            WHERE course_id = %s
            ORDER BY etudiant_id, date_note DESC
        """, (course_id,))
        gradebook_notes = {}
        for gb_note in cursor.fetchall():
            etud_id = gb_note['etudiant_id']
            if etud_id not in gradebook_notes:
                gradebook_notes[etud_id] = []
            gradebook_notes[etud_id].append(gb_note)

        conn.close()

        return render_template('professeur_course_manage.html',
                               course=course,
                               etudiants=etudiants,
                               presences=presences,
                               documents=documents,
                               notes_data=notes_data,
                               lectures=lectures,
                               exams=exams,
                               assignments=assignments,
                               gradebook_notes=gradebook_notes,
                               today=today,
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''))

    except Exception as e:
        import traceback
        error_msg = f"Erreur gestion module: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        print(f"Course ID: {course_id}, User ID: {session.get('user_id')}")
        flash(f"Erreur lors du chargement de la page de gestion: {str(e)}", "error")
        return redirect(url_for('professeur_emploi_temps'))

# Routes POST pour la gestion du module
@app.route('/professeur/course/<int:course_id>/presences', methods=['GET'])
@login_required
def get_presences(course_id):
    """Récupérer les présences pour une date donnée"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        date = request.args.get('date')
        if not date:
            return jsonify({'success': False, 'message': 'Date requise'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        year_id = get_current_year_id()
        if year_id:
            cursor.execute("""
                SELECT etudiant_id, statut, commentaire
                FROM presences
                WHERE course_id = %s AND date_cours = %s AND annee_academique_id = %s
            """, (course_id, date, year_id))
        else:
            cursor.execute("""
                SELECT etudiant_id, statut, commentaire
                FROM presences
                WHERE course_id = %s AND date_cours = %s
            """, (course_id, date))

        presences = cursor.fetchall()
        conn.close()

        return jsonify({'success': True, 'presences': presences})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/presences/save', methods=['POST'])
@login_required
def save_course_presences(course_id):
    """Sauvegarder les présences pour un cours"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        date = data.get('date_cours') or data.get('date')  # Support des deux formats
        if not date:
            # Si aucune date n'est fournie, utiliser la date du jour
            from datetime import datetime
            date = datetime.now().strftime('%Y-%m-%d')
        presences = data.get('presences', {})  # Peut être un objet ou une liste

        conn = get_db_connection()
        cursor = conn.cursor()
        cur_d = conn.cursor(dictionary=True)

        professeur_id = session['user_id']
        year_id = get_current_year_id()

        previous_statuts = {}
        if year_id:
            cur_d.execute("""
                SELECT etudiant_id, statut FROM presences
                WHERE course_id = %s AND date_cours = %s AND annee_academique_id = %s
            """, (course_id, date, year_id))
        else:
            cur_d.execute("""
                SELECT etudiant_id, statut FROM presences
                WHERE course_id = %s AND date_cours = %s
            """, (course_id, date))
        for row in cur_d.fetchall():
            previous_statuts[row['etudiant_id']] = row['statut']
        cur_d.close()

        # Gérer les deux formats : liste ou dictionnaire
        if isinstance(presences, dict):
            # Format dictionnaire : {etudiant_id: {statut: ..., commentaire: ...}}
            for etudiant_id, presence_info in presences.items():
                statut = presence_info.get('statut', 'unspecified')
                commentaire = presence_info.get('commentaire', '')

                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE statut = %s, commentaire = %s, professeur_id = %s, updated_at = NOW()
                """, (etudiant_id, course_id, professeur_id, date, statut, commentaire, year_id, tenant.current_school_id(),
                      statut, commentaire, professeur_id))
        else:
            # Format liste : [{'etudiant_id': ..., 'statut': ..., ...}, ...]
            for p in presences:
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE statut = %s, commentaire = %s, professeur_id = %s, updated_at = NOW()
                """, (p.get('etudiant_id') or p.get('etudiantId'), course_id, professeur_id, date,
                      p.get('statut', 'unspecified'), p.get('commentaire', ''), year_id, tenant.current_school_id(),
                      p.get('statut', 'unspecified'), p.get('commentaire', ''), professeur_id))
        
        conn.commit()

        process_absence_notifications(conn, course_id, date, presences, professeur_id, previous_statuts)

        conn.close()
        
        return jsonify({'success': True, 'message': 'Présences enregistrées'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# 🎯 ROUTES POUR SCAN QR CODE ET PRÉSENCE AUTOMATIQUE
@app.route('/professeur/scan-qr')
@login_required
def professeur_scan_qr():
    """Scanner QR — accessible uniquement depuis Gérer le cours > Présences."""
    if session.get('role') != 'professeur':
        flash("Accès refusé.", "danger")
        return redirect(url_for('login'))

    course_id = request.args.get('course_id', type=int)
    if not course_id:
        flash("Sélectionnez un cours puis ouvrez Présences pour scanner.", "info")
        return redirect(url_for('professeur_emploi_temps'))

    conn = get_db_connection()
    course = None
    if conn:
        cursor = conn.cursor(dictionary=True)
        course = _professor_owns_course(cursor, course_id, session.get('user_id'))
        conn.close()

    if not course:
        flash("Cours non trouvé ou accès refusé.", "danger")
        return redirect(url_for('professeur_emploi_temps'))

    return render_template('prof_scan_qr.html', course=course)


@app.route('/professeur/display-qr')
@login_required
def professeur_display_qr():
    """QR plein écran — accessible uniquement depuis Gérer le cours > Présences."""
    if session.get('role') != 'professeur':
        flash("Accès refusé.", "danger")
        return redirect(url_for('login'))

    course_id = request.args.get('course_id', type=int)
    if not course_id:
        flash("Sélectionnez un cours puis ouvrez Présences pour afficher le QR.", "info")
        return redirect(url_for('professeur_emploi_temps'))

    conn = get_db_connection()
    course = None
    if conn:
        cursor = conn.cursor(dictionary=True)
        course = _professor_owns_course(cursor, course_id, session.get('user_id'))
        conn.close()

    if not course:
        flash("Cours non trouvé ou accès refusé.", "danger")
        return redirect(url_for('professeur_emploi_temps'))

    return render_template('prof_display_qr.html', course=course)


@app.route('/student/scan-entrance')
@login_required
def student_scan_entrance():
    """Page pour que l'étudiant scanne le QR code à l'entrée"""
    if session.get('role') != 'etudiant':
        flash("Accès refusé.", "danger")
        return redirect(url_for('login'))

    return render_template('student_scan_entrance.html')


@app.route('/api/mark-presence-qr', methods=['POST'])
@login_required
def mark_presence_qr():
    """API pour marquer la présence via scan QR code"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        student_data = data.get('student_data')
        course_id = data.get('course_id')

        if not student_data or not course_id:
            return jsonify({'success': False, 'message': 'Données manquantes'}), 400

        # Vérifier la signature du QR code
        signature_received = student_data.get('signature')
        if not signature_received:
            return jsonify({'success': False, 'message': 'QR code non sécurisé'}), 400

        # Recréer la signature pour vérification
        data_to_verify = {
            'user_id': student_data.get('user_id'),
            'nom': student_data.get('nom'),
            'prenom': student_data.get('prenom'),
            'filiere': student_data.get('filiere'),
            'niveau': student_data.get('niveau'),
            'type': student_data.get('type'),
            'timestamp': student_data.get('timestamp')
        }
        data_string = json.dumps(data_to_verify, sort_keys=True)
        signature_expected = hashlib.sha256(f"{data_string}ADSCLASS_SECRET_2024".encode()).hexdigest()[:16]

        if signature_received != signature_expected:
            return jsonify({'success': False, 'message': 'QR code invalide ou falsifié'}), 400

        # Marquer la présence
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion à la base de données'}), 500

        cursor = conn.cursor(dictionary=True)
        etudiant_id = student_data.get('user_id')
        professeur_id = session.get('user_id')
        school_id = tenant.current_school_id()
        date_today = datetime.now().strftime('%Y-%m-%d')

        # Isolation multi-tenant : le cours doit appartenir au professeur ET a son ecole.
        course = _professor_owns_course(cursor, course_id, professeur_id)
        if not course or course.get('school_id') != school_id:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        # Isolation multi-tenant : la carte scannee doit etre celle d'un etudiant de
        # la meme ecole (un QR signe avec le secret global ne doit jamais permettre
        # de marquer une presence cross-tenant).
        cursor.execute("""
            SELECT id FROM users
            WHERE id = %s AND role = 'etudiant' AND school_id = %s
        """, (etudiant_id, school_id))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'success': False, 'message': 'Étudiant introuvable dans cette école'}), 403

        # Vérifier si la présence existe déjà
        cursor.execute("""
            SELECT id FROM presences
            WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s AND school_id = %s
        """, (etudiant_id, course_id, date_today, school_id))

        existing = cursor.fetchone()

        if existing:
            # Mettre à jour
            cursor.execute("""
                UPDATE presences
                SET statut = 'present', updated_at = NOW()
                WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s AND school_id = %s
            """, (etudiant_id, course_id, date_today, school_id))
            message = 'Présence mise à jour'
        else:
            # Insérer
            cursor.execute("""
                INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, school_id)
                VALUES (%s, %s, %s, %s, 'present', %s)
            """, (etudiant_id, course_id, professeur_id, date_today, school_id))
            message = 'Présence enregistrée'

        conn.commit()

        # Récupérer les infos de l'étudiant
        nom_complet = f"{student_data.get('prenom')} {student_data.get('nom')}"

        conn.close()

        return jsonify({
            'success': True,
            'message': message,
            'student': {
                'nom': student_data.get('nom'),
                'prenom': student_data.get('prenom'),
                'nom_complet': nom_complet,
                'filiere': student_data.get('filiere'),
                'niveau': student_data.get('niveau')
            }
        })

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Erreur mark_presence_qr: {error_details}")
        return jsonify({'success': False, 'message': f'Erreur serveur: {str(e)}'}), 500


@app.route('/api/course/<int:course_id>/students-presence', methods=['GET'])
@login_required
def get_students_presence(course_id):
    """Récupérer la liste des étudiants inscrits au cours avec leur statut de présence"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        date_today = datetime.now().strftime('%Y-%m-%d')
        user_id = session.get('user_id')

        course = _professor_owns_course(cursor, course_id, user_id)
        if not course:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        class_students = get_course_class_students(cursor, course_id, course)
        student_ids = [s['id'] for s in class_students]

        if not student_ids:
            conn.close()
            return jsonify({
                'success': True,
                'course': {'nom_cours': course['nom_cours'], 'filiere': course['filiere'], 'niveau': course.get('niveau')},
                'presents': [], 'absents': [], 'total': 0, 'nb_presents': 0, 'nb_absents': 0, 'taux_presence': 0
            })

        placeholders = ','.join(['%s'] * len(student_ids))
        cursor.execute(f"""
            SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau,
                   p.statut, p.created_at as heure_scan
            FROM users u
            LEFT JOIN presences p ON u.id = p.etudiant_id
                AND p.course_id = %s AND p.date_cours = %s
            WHERE u.id IN ({placeholders})
            ORDER BY u.nom, u.prenom
        """, (course_id, date_today, *student_ids))

        students = cursor.fetchall()
        conn.close()

        # Séparer présents et absents
        presents = []
        absents = []

        for student in students:
            student_info = {
                'id': student['id'],
                'nom': student['nom'],
                'prenom': student['prenom'],
                'nom_complet': f"{student['prenom']} {student['nom']}",
                'email': student['email'],
                'filiere': student['filiere'],
                'niveau': student['niveau'],
                'heure_scan': student['heure_scan'].strftime('%H:%M:%S') if student['heure_scan'] else None
            }

            if student['statut'] == 'present':
                presents.append(student_info)
            else:
                absents.append(student_info)

        return jsonify({
            'success': True,
            'course': {'nom_cours': course['nom_cours'], 'filiere': course['filiere'], 'niveau': course.get('niveau')},
            'presents': presents,
            'absents': absents,
            'total': len(students),
            'nb_presents': len(presents),
            'nb_absents': len(absents),
            'taux_presence': round((len(presents) / len(students) * 100) if students else 0, 1)
        })

    except Exception as e:
        import traceback
        print(f"Erreur get_students_presence: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/mark-all-present', methods=['POST'])
@login_required
def mark_all_present():
    """Marquer tous les étudiants d'un cours comme présents"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        course_id = data.get('course_id')

        if not course_id:
            return jsonify({'success': False, 'message': 'Course ID manquant'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        professeur_id = session.get('user_id')
        date_today = datetime.now().strftime('%Y-%m-%d')

        course = _professor_owns_course(cursor, course_id, professeur_id)
        if not course:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        students = get_course_class_students(cursor, course_id, course)

        count = 0
        for student in students:
            etudiant_id = student['id']

            # Vérifier si existe déjà
            cursor.execute("""
                SELECT id FROM presences
                WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s
            """, (etudiant_id, course_id, date_today))

            existing = cursor.fetchone()

            if existing:
                # Mettre à jour
                cursor.execute("""
                    UPDATE presences
                    SET statut = 'present', updated_at = NOW()
                    WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s
                """, (etudiant_id, course_id, date_today))
            else:
                # Insérer
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, school_id)
                    VALUES (%s, %s, %s, %s, 'present', %s)
                """, (etudiant_id, course_id, professeur_id, date_today, tenant.current_school_id()))

            count += 1

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'{count} étudiants marqués comme présents',
            'count': count
        })

    except Exception as e:
        import traceback
        print(f"Erreur mark_all_present: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/mark-all-absent', methods=['POST'])
@login_required
def mark_all_absent():
    """Marquer tous les étudiants d'un cours comme absents"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        course_id = data.get('course_id')

        if not course_id:
            return jsonify({'success': False, 'message': 'Course ID manquant'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        professeur_id = session.get('user_id')
        date_today = datetime.now().strftime('%Y-%m-%d')

        course = _professor_owns_course(cursor, course_id, professeur_id)
        if not course:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        students = get_course_class_students(cursor, course_id, course)

        count = 0
        for student in students:
            etudiant_id = student['id']

            # Vérifier si existe déjà
            cursor.execute("""
                SELECT id FROM presences
                WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s
            """, (etudiant_id, course_id, date_today))

            existing = cursor.fetchone()

            if existing:
                # Mettre à jour
                cursor.execute("""
                    UPDATE presences
                    SET statut = 'absent', updated_at = NOW()
                    WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s
                """, (etudiant_id, course_id, date_today))
            else:
                # Insérer
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, school_id)
                    VALUES (%s, %s, %s, %s, 'absent', %s)
                """, (etudiant_id, course_id, professeur_id, date_today, tenant.current_school_id()))

            count += 1

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'{count} étudiants marqués comme absents',
            'count': count
        })

    except Exception as e:
        import traceback
        print(f"Erreur mark_all_absent: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/finalize-presence', methods=['POST'])
@login_required
def finalize_presence():
    """Finaliser la présence : marquer tous les non-scannés comme absents"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        course_id = data.get('course_id')

        if not course_id:
            return jsonify({'success': False, 'message': 'Course ID manquant'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        professeur_id = session.get('user_id')
        date_today = datetime.now().strftime('%Y-%m-%d')

        course = _professor_owns_course(cursor, course_id, professeur_id)
        if not course:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        students = get_course_class_students(cursor, course_id, course)

        absents_count = 0
        presents_count = 0
        newly_absent = []

        for student in students:
            etudiant_id = student['id']

            # Vérifier si existe déjà
            cursor.execute("""
                SELECT id, statut FROM presences
                WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s
            """, (etudiant_id, course_id, date_today))

            existing = cursor.fetchone()

            if existing:
                # Déjà enregistré, compter
                if existing['statut'] == 'present':
                    presents_count += 1
                else:
                    absents_count += 1
            else:
                # Pas encore enregistré = absent
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, school_id)
                    VALUES (%s, %s, %s, %s, 'absent', %s)
                """, (etudiant_id, course_id, professeur_id, date_today, tenant.current_school_id()))
                absents_count += 1
                newly_absent.append({'etudiant_id': etudiant_id, 'statut': 'absent'})

        conn.commit()
        conn.close()

        if newly_absent:
            conn2 = get_db_connection()
            if conn2:
                process_absence_notifications(conn2, course_id, date_today, newly_absent, professeur_id, {})
                conn2.close()

        total = presents_count + absents_count
        taux_presence = round((presents_count / total * 100), 2) if total > 0 else 0

        return jsonify({
            'success': True,
            'message': 'Présence finalisée',
            'presents': presents_count,
            'absents': absents_count,
            'total': total,
            'taux_presence': taux_presence
        })

    except Exception as e:
        import traceback
        print(f"Erreur finalize_presence: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/generate-course-qr/<int:course_id>')
@login_required
def generate_course_qr(course_id):
    """Générer un QR code unique pour un cours (affiché à l'entrée de la classe)"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        professeur_id = session.get('user_id')

        # Récupérer les infos complètes du cours (emploi_temps ou professeur_id)
        cursor.execute("""
            SELECT c.id, c.nom_cours, c.filiere, c.niveau, c.professeur_id,
                   c.heure_debut, c.heure_fin, c.salle, c.jour_semaine,
                   u.prenom, u.nom
            FROM courses c
            LEFT JOIN emploi_temps et ON c.id = et.course_id AND et.user_id = %s AND et.role = 'professeur'
            LEFT JOIN users u ON COALESCE(c.professeur_id, et.user_id) = u.id
            WHERE c.id = %s AND (c.professeur_id = %s OR et.user_id IS NOT NULL)
        """, (professeur_id, course_id, professeur_id))
        course = cursor.fetchone()
        conn.close()

        if not course:
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        # Créer les données du QR code avec TOUTES les informations
        now = datetime.now()
        date_today = now.strftime('%Y-%m-%d')
        timestamp = now.isoformat()

        # Expiration : 30 secondes (pour rotation automatique)
        expiry = (now + timedelta(seconds=30)).isoformat()

        # Convertir les heures en string (timedelta ou time)
        heure_debut_str = ''
        heure_fin_str = ''
        if course['heure_debut']:
            if isinstance(course['heure_debut'], timedelta):
                total_seconds = int(course['heure_debut'].total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                heure_debut_str = f"{hours:02d}:{minutes:02d}"
            else:
                heure_debut_str = course['heure_debut'].strftime('%H:%M')

        if course['heure_fin']:
            if isinstance(course['heure_fin'], timedelta):
                total_seconds = int(course['heure_fin'].total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                heure_fin_str = f"{hours:02d}:{minutes:02d}"
            else:
                heure_fin_str = course['heure_fin'].strftime('%H:%M')

        qr_data = {
            'type': 'course_entrance',
            'course_id': course_id,
            'course_name': course['nom_cours'],
            'filiere': course['filiere'],
            'niveau': course['niveau'],
            'salle': course['salle'] if course['salle'] else 'Non spécifiée',
            'professeur': f"{course['prenom']} {course['nom']}" if course['prenom'] else 'Professeur',
            'jour': course['jour_semaine'] if course['jour_semaine'] else '',
            'heure_debut': heure_debut_str,
            'heure_fin': heure_fin_str,
            'date': date_today,
            'timestamp': timestamp,
            'expiry': expiry  # QR code expire après 30 secondes
        }

        # Créer une signature sécurisée
        data_string = json.dumps(qr_data, sort_keys=True)
        signature = hashlib.sha256(f"{data_string}ADSCLASS_SECRET_2024".encode()).hexdigest()[:16]
        qr_data['signature'] = signature

        # Encoder en JSON
        qr_json = json.dumps(qr_data)

        # Générer le QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_json)
        qr.make(fit=True)

        # Créer l'image
        img = qr.make_image(fill_color="black", back_color="white")

        # Convertir en base64
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()

        qr_code_data = f"data:image/png;base64,{img_str}"

        return jsonify({
            'success': True,
            'qr_code': qr_code_data,
            'course_name': course['nom_cours'],
            'date': date_today
        })

    except Exception as e:
        import traceback
        print(f"Erreur generate_course_qr: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/mark-presence-entrance', methods=['POST'])
@login_required
def mark_presence_entrance():
    """Marquer la présence d'un étudiant qui scanne le QR code à l'entrée"""
    try:
        if session.get('role') != 'etudiant':
            return jsonify({'success': False, 'message': 'Accès refusé - Réservé aux étudiants'}), 403

        data = request.get_json()
        qr_data = data.get('qr_data')

        if not qr_data:
            return jsonify({'success': False, 'message': 'Données QR manquantes'}), 400

        # Vérifier la signature
        signature_received = qr_data.get('signature')
        if not signature_received:
            return jsonify({'success': False, 'message': 'QR code non sécurisé'}), 400

        # Recréer la signature pour vérification
        data_to_verify = {
            'type': qr_data.get('type'),
            'course_id': qr_data.get('course_id'),
            'course_name': qr_data.get('course_name'),
            'filiere': qr_data.get('filiere'),
            'niveau': qr_data.get('niveau'),
            'salle': qr_data.get('salle'),
            'professeur': qr_data.get('professeur'),
            'jour': qr_data.get('jour'),
            'heure_debut': qr_data.get('heure_debut'),
            'heure_fin': qr_data.get('heure_fin'),
            'date': qr_data.get('date'),
            'timestamp': qr_data.get('timestamp'),
            'expiry': qr_data.get('expiry')
        }
        data_string = json.dumps(data_to_verify, sort_keys=True)
        signature_expected = hashlib.sha256(f"{data_string}ADSCLASS_SECRET_2024".encode()).hexdigest()[:16]

        if signature_received != signature_expected:
            return jsonify({'success': False, 'message': '❌ QR code invalide ou falsifié'}), 400

        # Vérifier que c'est bien un QR code d'entrée de cours
        if qr_data.get('type') != 'course_entrance':
            return jsonify({'success': False, 'message': '❌ Type de QR code incorrect'}), 400

        # ⏱️ VALIDATION 1 : Vérifier l'expiration du QR code (30 secondes)
        expiry_str = qr_data.get('expiry')
        if expiry_str:
            try:
                expiry_time = datetime.fromisoformat(expiry_str)
                if datetime.now() > expiry_time:
                    return jsonify({
                        'success': False,
                        'message': '⏱️ QR code expiré. Veuillez scanner le nouveau QR code affiché à l\'écran.'
                    }), 400
            except:
                pass  # Si erreur de parsing, on continue

        # Récupérer les infos de l'étudiant
        etudiant_id = session.get('user_id')
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT nom, prenom, filiere, niveau
            FROM users
            WHERE id = %s AND role = 'etudiant' AND school_id = %s
        """, (etudiant_id, tenant.current_school_id()))
        student = cursor.fetchone()

        if not student:
            conn.close()
            return jsonify({'success': False, 'message': 'Étudiant non trouvé'}), 404

        # Vérifier que l'étudiant est dans la bonne filière/niveau
        if student['filiere'] != qr_data.get('filiere') or student['niveau'] != qr_data.get('niveau'):
            conn.close()
            return jsonify({
                'success': False,
                'message': f"Ce cours est pour {qr_data.get('filiere')} - {qr_data.get('niveau')}. Vous êtes en {student['filiere']} - {student['niveau']}."
            }), 403

        course_id = qr_data.get('course_id')
        date_cours = qr_data.get('date')
        date_today = datetime.now().strftime('%Y-%m-%d')

        # Vérifier que le QR code est pour aujourd'hui
        if date_cours != date_today:
            conn.close()
            return jsonify({
                'success': False,
                'message': f'📅 Ce QR code est pour le {date_cours}. Aujourd\'hui nous sommes le {date_today}.'
            }), 400

        # Récupérer les infos complètes du cours (avec heure_debut et heure_fin).
        # Scopé école : un QR (signé avec un secret global) d'un cours d'une autre
        # école ne doit jamais permettre de marquer une présence cross-tenant.
        cursor.execute("""
            SELECT professeur_id, heure_debut, heure_fin, nom_cours
            FROM courses
            WHERE id = %s AND school_id = %s
        """, (course_id, tenant.current_school_id()))
        course = cursor.fetchone()

        if not course:
            conn.close()
            return jsonify({'success': False, 'message': '❌ Cours non trouvé'}), 404

        professeur_id = course['professeur_id']

        # ⏰ VALIDATION 2 : Vérifier la fenêtre temporelle (1 heure après le début du cours)
        if course['heure_debut']:
            now = datetime.now()
            today_date = now.date()

            # Convertir heure_debut en objet time
            if isinstance(course['heure_debut'], timedelta):
                total_seconds = int(course['heure_debut'].total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                from datetime import time as dt_time
                heure_debut_time = dt_time(hours, minutes)
            else:
                heure_debut_time = course['heure_debut']

            # Créer un datetime pour l'heure de début du cours aujourd'hui
            course_start = datetime.combine(today_date, heure_debut_time)

            # Fenêtre de 1 heure après le début
            window_end = course_start + timedelta(hours=1)

            # Vérifier si on est dans la fenêtre
            if now < course_start:
                conn.close()
                heure_debut_str = heure_debut_time.strftime('%H:%M')
                return jsonify({
                    'success': False,
                    'message': f'⏰ Le cours n\'a pas encore commencé. Début à {heure_debut_str}.'
                }), 400

            if now > window_end:
                conn.close()
                heure_debut_str = heure_debut_time.strftime('%H:%M')
                window_end_str = window_end.strftime('%H:%M')
                return jsonify({
                    'success': False,
                    'message': f'⏰ La fenêtre de présence est fermée.\n\nLe cours a commencé à {heure_debut_str}.\nVous aviez jusqu\'à {window_end_str} pour marquer votre présence (1 heure après le début).\n\nContactez votre professeur si vous étiez présent.'
                }), 400

        # Vérifier si la présence existe déjà (scopé école)
        cursor.execute("""
            SELECT id, statut FROM presences
            WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s AND school_id = %s
        """, (etudiant_id, course_id, date_today, tenant.current_school_id()))
        existing = cursor.fetchone()

        if existing:
            if existing['statut'] == 'present':
                conn.close()
                return jsonify({
                    'success': True,
                    'message': f'Présence déjà enregistrée pour {student["prenom"]} {student["nom"]} !',
                    'already_marked': True
                })
            else:
                # Mettre à jour de absent à present
                cursor.execute("""
                    UPDATE presences
                    SET statut = 'present', updated_at = NOW()
                    WHERE etudiant_id = %s AND course_id = %s AND date_cours = %s AND school_id = %s
                """, (etudiant_id, course_id, date_today, tenant.current_school_id()))
                message = 'Présence mise à jour'
        else:
            # Insérer nouvelle présence
            cursor.execute("""
                INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, school_id)
                VALUES (%s, %s, %s, %s, 'present', %s)
            """, (etudiant_id, course_id, professeur_id, date_today, tenant.current_school_id()))
            message = 'Présence enregistrée'

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'✅ {message} pour {student["prenom"]} {student["nom"]} !',
            'student': {
                'nom': student['nom'],
                'prenom': student['prenom'],
                'filiere': student['filiere'],
                'niveau': student['niveau']
            },
            'course': qr_data.get('course_name'),
            'course_info': {
                'nom': qr_data.get('course_name'),
                'professeur': qr_data.get('professeur'),
                'salle': qr_data.get('salle'),
                'heure_debut': qr_data.get('heure_debut'),
                'heure_fin': qr_data.get('heure_fin'),
                'date': qr_data.get('date'),
                'filiere': qr_data.get('filiere'),
                'niveau': qr_data.get('niveau')
            }
        })

    except Exception as e:
        import traceback
        print(f"Erreur mark_presence_entrance: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/professeur/course/<int:course_id>/lecture/add', methods=['POST'])
@login_required
def add_lecture(course_id):
    """Ajouter une séance de cours"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        cursor = get_db_connection().cursor()

        cursor.execute("""
            INSERT INTO lectures (course_id, titre, description, contenu, date_seance, ordre, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('titre'), data.get('description'), data.get('contenu'),
              data.get('date_seance'), data.get('ordre', 0), tenant.current_school_id()))

        get_db_connection().commit()
        get_db_connection().close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/exam/add', methods=['POST'])
@login_required
def add_exam(course_id):
    """Créer un examen"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        cursor = get_db_connection().cursor()
        
        cursor.execute("""
            INSERT INTO exams (course_id, type_examen, titre, date_examen, coefficient, description, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('type_examen'), data.get('titre'), data.get('date_examen'),
              data.get('coefficient', 1.0), data.get('description'), tenant.current_school_id()))
        
        get_db_connection().commit()
        get_db_connection().close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/assignment/add', methods=['POST'])
@login_required
def add_assignment(course_id):
    """Publier un devoir"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        cursor = get_db_connection().cursor()
        
        cursor.execute("""
            INSERT INTO assignments (course_id, titre, description, date_publication, date_limite, type_assignment, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('titre'), data.get('description'), data.get('date_publication'),
              data.get('date_limite'), data.get('type_assignment', 'devoir'), tenant.current_school_id()))
        
        get_db_connection().commit()
        get_db_connection().close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Routes pour GET/EDIT/DELETE des Lectures
@app.route('/professeur/course/<int:course_id>/lecture/<int:lecture_id>', methods=['GET'])
@login_required
def get_lecture(course_id, lecture_id):
    """Récupérer une séance"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM lectures WHERE id = %s AND course_id = %s", (lecture_id, course_id))
        lecture = cursor.fetchone()
        conn.close()
        if lecture:
            return jsonify({'success': True, 'lecture': lecture})
        return jsonify({'success': False, 'message': 'Séance non trouvée'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/lecture/<int:lecture_id>/edit', methods=['POST'])
@login_required
def edit_lecture(course_id, lecture_id):
    """Modifier une séance"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE lectures SET titre = %s, description = %s, contenu = %s, date_seance = %s
            WHERE id = %s AND course_id = %s AND school_id = %s
        """, (data.get('titre'), data.get('description'), data.get('contenu'),
              data.get('date_seance') or None, lecture_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/lecture/<int:lecture_id>/delete', methods=['POST'])
@login_required
def delete_lecture(course_id, lecture_id):
    """Supprimer une séance"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lectures WHERE id = %s AND course_id = %s AND school_id = %s",
                       (lecture_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Routes pour GET/EDIT/DELETE des Exams
@app.route('/professeur/course/<int:course_id>/exam/<int:exam_id>', methods=['GET'])
@login_required
def get_exam(course_id, exam_id):
    """Récupérer un examen"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM exams WHERE id = %s AND course_id = %s", (exam_id, course_id))
        exam = cursor.fetchone()
        conn.close()
        if exam:
            return jsonify({'success': True, 'exam': exam})
        return jsonify({'success': False, 'message': 'Examen non trouvé'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/exam/<int:exam_id>/edit', methods=['POST'])
@login_required
def edit_exam(course_id, exam_id):
    """Modifier un examen"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE exams SET titre = %s, type_examen = %s, date_examen = %s, coefficient = %s, description = %s
            WHERE id = %s AND course_id = %s AND school_id = %s
        """, (data.get('titre'), data.get('type_examen'), data.get('date_examen') or None,
              data.get('coefficient', 1.0), data.get('description'), exam_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/exam/<int:exam_id>/delete', methods=['POST'])
@login_required
def delete_exam(course_id, exam_id):
    """Supprimer un examen"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM exams WHERE id = %s AND course_id = %s AND school_id = %s",
                       (exam_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Routes pour GET/EDIT/DELETE des Assignments
@app.route('/professeur/course/<int:course_id>/assignment/<int:assignment_id>', methods=['GET'])
@login_required
def get_assignment(course_id, assignment_id):
    """Récupérer un devoir"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM assignments WHERE id = %s AND course_id = %s", (assignment_id, course_id))
        assignment = cursor.fetchone()
        conn.close()
        if assignment:
            return jsonify({'success': True, 'assignment': assignment})
        return jsonify({'success': False, 'message': 'Devoir non trouvé'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/assignment/<int:assignment_id>/edit', methods=['POST'])
@login_required
def edit_assignment(course_id, assignment_id):
    """Modifier un devoir"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE assignments SET titre = %s, description = %s, date_publication = %s,
            date_limite = %s, type_assignment = %s
            WHERE id = %s AND course_id = %s AND school_id = %s
        """, (data.get('titre'), data.get('description'), data.get('date_publication') or None,
              data.get('date_limite') or None, data.get('type_assignment'), assignment_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/assignment/<int:assignment_id>/delete', methods=['POST'])
@login_required
def delete_assignment(course_id, assignment_id):
    """Supprimer un devoir"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM assignments WHERE id = %s AND course_id = %s AND school_id = %s",
                       (assignment_id, course_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/assignment/<int:assignment_id>/submissions')
@login_required
def view_assignment_submissions(course_id, assignment_id):
    """Voir les rendus d'un devoir"""
    if session.get('role') != 'professeur':
        return redirect(url_for('login'))
    # Pour l'instant, rediriger vers la page de gestion du cours
    flash('Fonctionnalité de visualisation des rendus en cours de développement', 'info')
    return redirect(url_for('professeur_course_manage', course_id=course_id))

def sync_gradebook_to_notes(course_id, etudiant_id, professeur_id=None):
    """Synchroniser les notes du gradebook vers la table notes pour l'administrateur"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        cursor = conn.cursor(dictionary=True)
        
        # Récupérer le nom du cours
        cursor.execute("SELECT nom_cours, filiere, niveau FROM courses WHERE id = %s", (course_id,))
        course = cursor.fetchone()
        if not course:
            conn.close()
            return False
        
        nom_cours = course['nom_cours']
        
        # Si professeur_id n'est pas fourni, le récupérer depuis le gradebook
        if professeur_id is None:
            cursor.execute("""
                SELECT professeur_id FROM gradebook 
                WHERE course_id = %s AND etudiant_id = %s 
                LIMIT 1
            """, (course_id, etudiant_id))
            prof_result = cursor.fetchone()
            if prof_result:
                professeur_id = prof_result['professeur_id']
        
        # Vérifier si la colonne professeur_id existe dans notes
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'professeur_id'")
        professeur_id_exists = cursor.fetchone() is not None
        if not professeur_id_exists and professeur_id:
            try:
                cursor.execute("ALTER TABLE notes ADD COLUMN professeur_id INT AFTER etudiant_id")
                cursor.execute("ALTER TABLE notes ADD FOREIGN KEY (professeur_id) REFERENCES users(id) ON DELETE CASCADE")
                conn.commit()
            except Exception as e:
                print(f"Erreur ajout colonne professeur_id: {e}")
        
        # Vérifier si la colonne visible existe dans notes
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
        visible_exists = cursor.fetchone() is not None
        if not visible_exists:
            try:
                cursor.execute("ALTER TABLE notes ADD COLUMN visible TINYINT(1) DEFAULT 0")
                conn.commit()
            except Exception as e:
                print(f"Erreur ajout colonne visible: {e}")

        # Vérifier si la colonne annee_academique_id existe dans notes
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'annee_academique_id'")
        annee_exists = cursor.fetchone() is not None
        if not annee_exists:
            try:
                cursor.execute("ALTER TABLE notes ADD COLUMN annee_academique_id INT")
                conn.commit()
            except Exception as e:
                print(f"Erreur ajout colonne annee_academique_id: {e}")

        # Vérifier si la colonne semestre existe
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'semestre'")
        semestre_exists = cursor.fetchone() is not None
        
        # Récupérer toutes les notes du gradebook pour ce cours et cet étudiant
        cursor.execute("""
            SELECT type_note, note, coefficient 
            FROM gradebook 
            WHERE course_id = %s AND etudiant_id = %s
        """, (course_id, etudiant_id))
        
        gradebook_notes = cursor.fetchall()
        
        # Initialiser les valeurs
        cc1 = None
        cc2 = None
        participation = None
        examen = None
        
        # Regrouper les notes par type
        for gb_note in gradebook_notes:
            type_note = gb_note['type_note']
            note_value = gb_note['note']
            
            if type_note == 'CC1':
                cc1 = note_value
            elif type_note == 'CC2':
                cc2 = note_value
            elif type_note == 'Participation':
                participation = note_value
            elif type_note == 'Examen':
                examen = note_value
            # Rattrapage n'est pas dans la table notes, on l'ignore pour l'instant
        
        # Vérifier si une note existe déjà pour ce cours et cet étudiant
        if semestre_exists:
            cursor.execute("""
                SELECT id FROM notes 
                WHERE etudiant_id = %s AND nom_cours = %s AND semestre = 1
            """, (etudiant_id, nom_cours))
        else:
            cursor.execute("""
                SELECT id FROM notes 
                WHERE etudiant_id = %s AND nom_cours = %s
            """, (etudiant_id, nom_cours))
        
        existing = cursor.fetchone()
        
        if existing:
            # Mettre à jour la note existante
            if semestre_exists and professeur_id_exists and professeur_id:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s, professeur_id = %s
                    WHERE id = %s AND school_id = %s
                """, (cc1, cc2, participation, examen, professeur_id, existing['id'], tenant.current_school_id()))
            elif semestre_exists:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
                    WHERE id = %s AND school_id = %s
                """, (cc1, cc2, participation, examen, existing['id'], tenant.current_school_id()))
            elif professeur_id_exists and professeur_id:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s, professeur_id = %s
                    WHERE id = %s AND school_id = %s
                """, (cc1, cc2, participation, examen, professeur_id, existing['id'], tenant.current_school_id()))
            else:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
                    WHERE id = %s AND school_id = %s
                """, (cc1, cc2, participation, examen, existing['id'], tenant.current_school_id()))
        else:
            # Insérer une nouvelle note
            year_id = get_current_year_id()
            if semestre_exists and professeur_id_exists and professeur_id:
                cursor.execute("""
                    INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 0, %s, %s)
                """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
            elif semestre_exists:
                cursor.execute("""
                    INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, 0, %s, %s)
                """, (etudiant_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
            elif professeur_id_exists and professeur_id:
                if visible_exists:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen, visible, annee_academique_id, school_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
                    """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
                else:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen, annee_academique_id, school_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
            else:
                if visible_exists:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, visible, annee_academique_id, school_id)
                        VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
                    """, (etudiant_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
                else:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, annee_academique_id, school_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (etudiant_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erreur synchronisation gradebook vers notes: {e}")
        import traceback
        traceback.print_exc()
        return False

@app.route('/professeur/course/<int:course_id>/gradebook/add', methods=['POST'])
@login_required
def add_gradebook_note(course_id):
    """Ajouter une note au gradebook"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        from datetime import datetime
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Vérifier si une note existe déjà pour ce type
        cursor.execute("""
            SELECT id FROM gradebook 
            WHERE course_id = %s AND etudiant_id = %s AND type_note = %s
        """, (course_id, data.get('etudiant_id'), data.get('type_note')))
        
        existing = cursor.fetchone()
        
        professeur_id = session['user_id']
        
        if existing:
            # Mettre à jour la note existante
            cursor.execute("""
                UPDATE gradebook 
                SET note = %s, coefficient = %s, date_note = %s, professeur_id = %s, updated_at = NOW()
                WHERE id = %s AND school_id = %s
            """, (data.get('note'), data.get('coefficient', 1.0),
                  datetime.now().strftime('%Y-%m-%d'), professeur_id, existing[0], tenant.current_school_id()))
        else:
            # Insérer une nouvelle note
            year_id = get_current_year_id()
            cursor.execute("""
                INSERT INTO gradebook (course_id, etudiant_id, professeur_id, type_note, note, coefficient, date_note, annee_academique_id, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (course_id, data.get('etudiant_id'), professeur_id, data.get('type_note'), data.get('note'),
                  data.get('coefficient', 1.0), datetime.now().strftime('%Y-%m-%d'), year_id, tenant.current_school_id()))

        conn.commit()

        # Synchroniser vers la table notes
        sync_gradebook_to_notes(course_id, data.get('etudiant_id'), professeur_id)
        
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/gradebook/update', methods=['POST'])
@login_required
def update_gradebook_note(course_id):
    """Mettre à jour une note du gradebook directement"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        data = request.get_json()
        from datetime import datetime
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Mapper les types de notes
        type_mapping = {
            'CC1': 'CC1',
            'CC2': 'CC2',
            'Participation': 'Participation',
            'Examen': 'Examen',
            'Rattrapage': 'Rattrapage'
        }
        
        type_note = type_mapping.get(data.get('type_note'), data.get('type_note'))
        etudiant_id = data.get('etudiant_id')
        note_value = data.get('note')
        coefficient = data.get('coefficient', 1.0)
        
        professeur_id = session['user_id']
        
        if note_value is None or note_value == '':
            # Supprimer la note si elle est vide
            cursor.execute("""
                DELETE FROM gradebook
                WHERE course_id = %s AND etudiant_id = %s AND type_note = %s AND school_id = %s
            """, (course_id, etudiant_id, type_note, tenant.current_school_id()))
        else:
            # Vérifier si une note existe déjà
            cursor.execute("""
                SELECT id FROM gradebook 
                WHERE course_id = %s AND etudiant_id = %s AND type_note = %s
            """, (course_id, etudiant_id, type_note))
            
            existing = cursor.fetchone()
            
            if existing:
                # Mettre à jour
                cursor.execute("""
                    UPDATE gradebook 
                    SET note = %s, coefficient = %s, date_note = %s, professeur_id = %s, updated_at = NOW()
                    WHERE id = %s AND school_id = %s
                """, (float(note_value), float(coefficient), datetime.now().strftime('%Y-%m-%d'), professeur_id, existing[0], tenant.current_school_id()))
            else:
                # Insérer
                year_id = get_current_year_id()
                cursor.execute("""
                    INSERT INTO gradebook (course_id, etudiant_id, professeur_id, type_note, note, coefficient, date_note, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (course_id, etudiant_id, professeur_id, type_note, float(note_value), float(coefficient),
                      datetime.now().strftime('%Y-%m-%d'), year_id, tenant.current_school_id()))
        
        conn.commit()
        
        # Synchroniser vers la table notes si une note a été ajoutée/modifiée
        if note_value is not None and note_value != '':
            sync_gradebook_to_notes(course_id, etudiant_id, professeur_id)
        elif note_value is None or note_value == '':
            # Si la note est supprimée, synchroniser quand même pour mettre à jour
            sync_gradebook_to_notes(course_id, etudiant_id, professeur_id)
        
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/gradebook/calculate', methods=['POST'])
@login_required
def calculate_gradebook_averages(course_id):
    """Calculer les moyennes pour tous les étudiants du gradebook"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Récupérer tous les étudiants du cours
        cursor.execute("""
            SELECT u.id
            FROM users u
            JOIN courses c ON u.filiere = c.filiere
            WHERE c.id = %s AND u.role = 'etudiant' AND u.school_id = %s
        """, (course_id, tenant.current_school_id()))
        etudiants = cursor.fetchall()
        
        # Pour chaque étudiant, calculer la moyenne
        for etudiant in etudiants:
            etudiant_id = etudiant['id']
            
            # Récupérer toutes les notes avec leurs coefficients
            cursor.execute("""
                SELECT type_note, note, coefficient
                FROM gradebook
                WHERE course_id = %s AND etudiant_id = %s
            """, (course_id, etudiant_id))
            notes = cursor.fetchall()
            
            if notes:
                total = 0
                total_coef = 0
                for note in notes:
                    if note['note'] is not None:
                        total += note['note'] * note['coefficient']
                        total_coef += note['coefficient']
                
                # La moyenne est calculée côté client, on ne stocke pas ici
                # mais on pourrait ajouter une colonne moyenne dans gradebook si nécessaire
        
        conn.close()
        
        return jsonify({'success': True, 'message': 'Moyennes calculées'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/professeur/course/<int:course_id>/document/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete_course_document(course_id, doc_id):
    """Supprimer un document"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que le document appartient au cours
        cursor.execute("SELECT chemin_fichier FROM documents WHERE id = %s AND course_id = %s AND school_id = %s",
                       (doc_id, course_id, tenant.current_school_id()))
        doc = cursor.fetchone()
        
        if not doc:
            return jsonify({'success': False, 'message': 'Document non trouvé'}), 404

        # Supprimer le fichier
        import os
        file_path = os.path.join('uploads', doc['chemin_fichier'])
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Supprimer de la base
        cursor.execute("DELETE FROM documents WHERE id = %s AND school_id = %s",
                       (doc_id, tenant.current_school_id()))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


def _fetch_active_filieres():
    """Wrapper local: ouvre une connexion, retourne la liste des filières actives."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        return get_active_filieres(cursor, tenant.current_school_id())
    finally:
        conn.close()


# Route pour créer un compte professeur (pour l'admin)
def _init_professeur_classes_table():
    """Table de mapping professeurs <-> classes (filière+niveau).
    Idempotente, créée au démarrage."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS professeur_classes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                professeur_id INT NOT NULL,
                filiere_id INT NOT NULL,
                niveau VARCHAR(50) NOT NULL,
                school_id INT NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_prof_filiere_niveau (professeur_id, filiere_id, niveau),
                KEY idx_pc_prof (professeur_id),
                KEY idx_pc_filiere (filiere_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        cur.close()
    except Error:
        pass
    finally:
        conn.close()


def _commit_user_with_credentials(role_schema, form):
    """Crée un utilisateur (etudiant/professeur/admin) via enrich_user_payload.
    Retourne (user_id, credentials_dict) ou (None, error_message)."""
    payload = {
        'nom': (form.get('nom') or '').strip(),
        'prenom': (form.get('prenom') or '').strip(),
        'email': (form.get('email') or '').strip().lower() or None,
        'telephone': (form.get('telephone') or '').strip() or None,
        'specialite': (form.get('specialite') or '').strip() or None,
        'filiere': (form.get('filiere') or '').strip() or None,
        'niveau': (form.get('niveau') or '').strip() or None,
        'classe': (form.get('classe') or '').strip() or None,
        'password': (form.get('password') or '').strip() or None,
    }
    if not payload['nom'] or not payload['prenom']:
        return None, "Nom et prénom obligatoires."

    conn = get_db_connection()
    if not conn:
        return None, "Erreur de connexion à la base de données."

    try:
        cur = conn.cursor(dictionary=True)
        if payload['email']:
            cur.execute("SELECT id FROM users WHERE email = %s", (payload['email'],))
            if cur.fetchone():
                return None, f"L'email {payload['email']} est déjà utilisé."

        users_data, _profile, creds = enrich_user_payload(cur, role_schema, payload, {})

        cols = ['nom', 'prenom', 'email', 'password', 'role',
                'identifiant', 'password_temp', 'must_change_password',
                'telephone', 'specialite', 'filiere', 'niveau', 'classe', 'filiere_id']
        values = [users_data.get(c) for c in cols]
        cols = cols + ['school_id']
        values = values + [tenant.current_school_id()]
        placeholders = ','.join(['%s'] * len(cols))
        cur.execute(
            f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})",
            values
        )
        user_id = cur.lastrowid

        if users_data.get('role') == 'etudiant':
            try:
                from imports.credentials import post_enroll_student
                post_enroll_student(cur, user_id, users_data)
            except Exception:
                pass

        conn.commit()
        creds['user_id'] = user_id
        return user_id, creds
    except Error as e:
        conn.rollback()
        return None, f"Erreur base de données : {e}"
    finally:
        conn.close()


@app.route('/admin/add_professeur', methods=['GET', 'POST'])
@login_required
@admin_required
def add_professeur():
    """Création manuelle d'un professeur avec identifiants auto-générés."""
    if request.method == 'POST':
        user_id, result = _commit_user_with_credentials('professeurs', request.form)
        if not user_id:
            flash(result, "danger")
            return render_template('add_professeur.html',
                                   form=request.form,
                                   filieres=_fetch_active_filieres())
        flash(f"Professeur {result['prenom']} {result['nom']} créé. Identifiant : {result['identifiant']}", "success")
        return redirect(url_for('admin_professeur_credentials_print', user_id=user_id))

    return render_template('add_professeur.html', form={}, filieres=_fetch_active_filieres())


# ============================================================
# 👔 GESTION DES ADMINISTRATEURS
# ============================================================

@app.route('/admin/administrateurs')
@login_required
@admin_required
def admin_administrateurs():
    """Liste pro des administrateurs avec rôles et identifiants."""
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion.", "danger")
        return redirect(url_for('admin_home'))
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.nom, u.prenom, u.email, u.telephone, u.identifiant,
               u.password_temp, u.must_change_password, u.admin_role_id,
               r.nom AS role_nom, r.couleur AS role_couleur, r.icone AS role_icone
        FROM users u
        LEFT JOIN admin_roles r ON u.admin_role_id = r.id
        WHERE u.role = 'admin' AND u.school_id = %s
        ORDER BY r.priorite DESC, u.nom, u.prenom
    """, (tenant.current_school_id(),))
    admins = cursor.fetchall()
    conn.close()
    roles = PermissionManager.get_all_roles()
    return render_template('admin_administrateurs.html', admins=admins, roles=roles)


@app.route('/admin/administrateurs/new', methods=['GET', 'POST'])
@login_required
@admin_required
def add_administrateur():
    """Création manuelle d'un administrateur avec rôle optionnel."""
    roles = PermissionManager.get_all_roles()
    if request.method == 'POST':
        role_id = request.form.get('admin_role_id') or None
        user_id, result = _commit_user_with_credentials('administrateurs', request.form)
        if not user_id:
            flash(result, "danger")
            return render_template('add_administrateur.html', form=request.form, roles=roles)
        if role_id:
            try:
                PermissionManager.assign_role_to_user(user_id, int(role_id))
            except Exception:
                pass
        flash(f"Administrateur {result['prenom']} {result['nom']} créé. Identifiant : {result['identifiant']}", "success")
        return redirect(url_for('admin_administrateur_credentials_print', user_id=user_id))
    return render_template('add_administrateur.html', form={}, roles=roles)


@app.route('/admin/administrateurs/<int:user_id>/credentials/print')
@login_required
@admin_required
def admin_administrateur_credentials_print(user_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion.", "danger")
        return redirect(url_for('admin_administrateurs'))
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.nom, u.prenom, u.email, u.identifiant, u.password_temp,
               u.must_change_password, u.telephone, u.role,
               r.nom AS role_nom
        FROM users u
        LEFT JOIN admin_roles r ON u.admin_role_id = r.id
        WHERE u.id = %s AND u.role = 'admin' AND u.school_id = %s
    """, (user_id, tenant.current_school_id()))
    user = cursor.fetchone()
    conn.close()
    if not user:
        flash("Administrateur introuvable.", "danger")
        return redirect(url_for('admin_administrateurs'))
    return render_template('admin_student_credentials_print.html',
                           students=[user], single=True, nom_classe=user.get('role_nom') or '',
                           role_label='Administrateur')


@app.route('/admin/administrateurs/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_administrateur_delete(user_id):
    if user_id == session.get('user_id'):
        flash("Vous ne pouvez pas supprimer votre propre compte.", "warning")
        return redirect(url_for('admin_administrateurs'))
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion.", "danger")
        return redirect(url_for('admin_administrateurs'))
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s AND role = 'admin' AND school_id = %s",
                    (user_id, tenant.current_school_id()))
        conn.commit()
        flash("Administrateur supprimé.", "success")
    except Error as e:
        conn.rollback()
        flash(f"Suppression impossible : {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for('admin_administrateurs'))


# ============================================================
# 🎓 INSCRIPTION MANUELLE D'UN ÉTUDIANT
# ============================================================

@app.route('/admin/etudiants/inscription', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_inscription_etudiant():
    """Inscription manuelle d'un étudiant avec génération automatique des identifiants."""
    filieres = _fetch_active_filieres()
    niveaux = ['L1', 'L2', 'L3', 'M1', 'M2']
    if request.method == 'POST':
        user_id, result = _commit_user_with_credentials('students', request.form)
        if not user_id:
            flash(result, "danger")
            return render_template('admin_inscription_etudiant.html',
                                   form=request.form, filieres=filieres, niveaux=niveaux)
        flash(f"Étudiant {result['prenom']} {result['nom']} inscrit. Identifiant : {result['identifiant']}", "success")
        return redirect(url_for('admin_student_credentials_print', etudiant_id=user_id))
    return render_template('admin_inscription_etudiant.html',
                           form={}, filieres=filieres, niveaux=niveaux)



@app.route('/admin/depenses', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_depenses():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    if request.method == 'POST':
        date = request.form.get('date')
        nature = request.form.get('nature')
        montant_str = request.form.get('montant')

        if not date or not nature or not montant_str:
            flash("Tous les champs sont obligatoires.", "warning")
            return redirect(url_for('admin_depenses'))

        try:
            montant = float(montant_str)
            if montant <= 0:
                flash("Le montant doit être positif.", "warning")
                return redirect(url_for('admin_depenses'))

            montant = -abs(montant)  # Stocké en négatif

            cursor = conn.cursor()
            year_id = get_current_year_id()
            cursor.execute(
                "INSERT INTO depenses (date, description, montant, annee_academique_id, school_id) VALUES (%s, %s, %s, %s, %s)",
                (date, nature, montant, year_id, tenant.current_school_id())
            )
            conn.commit()
            flash("Dépense ajoutée avec succès.", "success")
            return redirect(url_for('admin_depenses'))

        except ValueError:
            flash("Montant invalide.", "danger")
            return redirect(url_for('admin_depenses'))

    year_id = get_current_year_id()
    cursor = conn.cursor(dictionary=True)
    if year_id:
        cursor.execute("SELECT * FROM depenses WHERE annee_academique_id = %s AND school_id = %s ORDER BY date DESC", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT * FROM depenses WHERE school_id = %s ORDER BY date DESC", (tenant.current_school_id(),))
    raw_depenses = cursor.fetchall()
    depenses = []

    for d in raw_depenses:
        depenses.append({
            'id': d['id'],
            'date': d['date'],
            'description': d['description'],
            'montant_brut': d['montant'],
            'montant_affiche': "{:,.0f}".format(abs(d['montant'])).replace(",", " ")
        })

    conn.close()
    return render_template('admin_depenses.html', depenses=depenses)


@app.route('/admin/finance/graphique')
@login_required
@admin_required
def finance_graphique():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    year_id = get_current_year_id()
    cursor = conn.cursor()

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
    total_recette = cursor.fetchone()[0]

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
    total_depense = cursor.fetchone()[0]

    conn.close()
    return render_template(
        'finance_graphique.html',
        total_recette=total_recette,
        total_depense=total_depense
    )

@app.route('/admin/finance')
@login_required
@admin_required
def admin_finance():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    year_id = get_current_year_id()
    cursor = conn.cursor(dictionary=True)

    # Récupérer les filières et niveaux pour les filtres
    cursor.execute("SELECT DISTINCT filiere FROM users WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s ORDER BY filiere", (tenant.current_school_id(),))
    filieres = [row['filiere'] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT niveau FROM users WHERE role = 'etudiant' AND niveau IS NOT NULL AND niveau != '' AND school_id = %s ORDER BY niveau", (tenant.current_school_id(),))
    niveaux = [row['niveau'] for row in cursor.fetchall()]

    # Filtrer par année académique
    if year_id:
        cursor.execute("""
            SELECT
                p.date,
                p.observation AS description,
                p.montant,
                'Recette' AS type,
                u.prenom,
                u.nom,
                u.filiere,
                u.niveau,
                u.id as etudiant_id
            FROM paiements p
            LEFT JOIN users u ON u.id = p.etudiant_id
            WHERE p.annee_academique_id = %s AND p.school_id = %s
            ORDER BY p.date DESC
        """, (year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT
                p.date,
                p.observation AS description,
                p.montant,
                'Recette' AS type,
                u.prenom,
                u.nom,
                u.filiere,
                u.niveau,
                u.id as etudiant_id
            FROM paiements p
            LEFT JOIN users u ON u.id = p.etudiant_id
            WHERE p.school_id = %s
            ORDER BY p.date DESC
        """, (tenant.current_school_id(),))
    recettes = cursor.fetchall()

    if year_id:
        cursor.execute("""
            SELECT
                d.date,
                d.description,
                d.montant,
                'Dépense' AS type,
                NULL AS prenom,
                NULL AS nom,
                NULL AS filiere,
                NULL AS niveau,
                NULL AS etudiant_id
            FROM depenses d
            WHERE d.annee_academique_id = %s AND d.school_id = %s
            ORDER BY d.date DESC
        """, (year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT
                d.date,
                d.description,
                d.montant,
                'Dépense' AS type,
                NULL AS prenom,
                NULL AS nom,
                NULL AS filiere,
                NULL AS niveau,
                NULL AS etudiant_id
            FROM depenses d
            WHERE d.school_id = %s
            ORDER BY d.date DESC
        """, (tenant.current_school_id(),))
    depenses = cursor.fetchall()

    transactions = recettes + depenses
    transactions = sorted(transactions, key=lambda t: t['date'], reverse=True)

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
    total_recette = cursor.fetchone()['IFNULL(SUM(montant), 0)']

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
    total_depense = cursor.fetchone()['IFNULL(SUM(montant), 0)']

    # Récupérer les étudiants avec leur statut de paiement
    if year_id:
        cursor.execute("""
            SELECT
                u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau,
                IFNULL(SUM(p.montant), 0) as total_paye
            FROM users u
            LEFT JOIN paiements p ON p.etudiant_id = u.id AND p.annee_academique_id = %s
            WHERE u.role = 'etudiant' AND u.school_id = %s
            GROUP BY u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau
            ORDER BY u.filiere, u.niveau, u.nom
        """, (year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT
                u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau,
                IFNULL(SUM(p.montant), 0) as total_paye
            FROM users u
            LEFT JOIN paiements p ON p.etudiant_id = u.id
            WHERE u.role = 'etudiant' AND u.school_id = %s
            GROUP BY u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau
            ORDER BY u.filiere, u.niveau, u.nom
        """, (tenant.current_school_id(),))
    etudiants = cursor.fetchall()

    conn.close()

    return render_template(
        "admin_finance.html",
        transactions=transactions,
        total_recette=total_recette,
        total_depense=total_depense,
        filieres=filieres,
        niveaux=niveaux,
        etudiants=etudiants
    )


# --- API: Finance live summary ---
@app.route('/admin/api/finance/summary')
@login_required
@admin_required
def api_finance_summary():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "db_connection_failed"}), 500

    year_id = get_current_year_id()
    cursor = conn.cursor()

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
    total_recette = cursor.fetchone()[0]

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
    total_depense = cursor.fetchone()[0]

    conn.close()

    try:
        recettes = float(total_recette)
    except Exception:
        recettes = 0.0
    try:
        depenses = abs(float(total_depense))
    except Exception:
        depenses = 0.0

    benefice = recettes - depenses
    ratio = (benefice / recettes * 100.0) if recettes > 0 else 0.0

    return jsonify({
        "recettes": recettes,
        "depenses": depenses,
        "benefice": benefice,
        "ratio": ratio,
        "ts": datetime.now().isoformat()
    })


# Décorateur pour vérifier le rôle admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/stats')
@login_required
@admin_required
def admin_stats():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    year_id = get_current_year_id()
    cursor = conn.cursor()

    # Statistiques des utilisateurs (ne dépendent pas de l'année)
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='etudiant' AND school_id = %s", (tenant.current_school_id(),))
    count_etudiants = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='professeur' AND school_id = %s", (tenant.current_school_id(),))
    count_professeurs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND school_id = %s", (tenant.current_school_id(),))
    count_admins = cursor.fetchone()[0]

    # Statistiques financières (filtrées par année)
    if year_id:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE school_id = %s", (tenant.current_school_id(),))
    total_recettes = cursor.fetchone()[0]

    if year_id:
        cursor.execute("SELECT IFNULL(SUM(ABS(montant)), 0) FROM depenses WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT IFNULL(SUM(ABS(montant)), 0) FROM depenses WHERE school_id = %s", (tenant.current_school_id(),))
    total_depenses = cursor.fetchone()[0]
    benefice_net = total_recettes - total_depenses

    # Statistiques des cours (filtrées par année)
    if year_id:
        cursor.execute("SELECT COUNT(*) FROM courses WHERE annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT COUNT(*) FROM courses WHERE school_id = %s", (tenant.current_school_id(),))
    count_courses = cursor.fetchone()[0]

    if year_id:
        cursor.execute("SELECT COUNT(*) FROM courses WHERE professeur_id IS NOT NULL AND professeur_nom != '' AND annee_academique_id = %s AND school_id = %s", (year_id, tenant.current_school_id()))
    else:
        cursor.execute("SELECT COUNT(*) FROM courses WHERE professeur_id IS NOT NULL AND professeur_nom != '' AND school_id = %s", (tenant.current_school_id(),))
    courses_with_prof = cursor.fetchone()[0]

    # Statistiques des paiements (filtrées par année)
    if year_id:
        cursor.execute("""
            SELECT COUNT(DISTINCT etudiant_id) FROM paiements
            WHERE annee_academique_id = %s AND school_id = %s AND etudiant_id IN (
                SELECT id FROM users WHERE role = 'etudiant' AND school_id = %s
            ) AND etudiant_id IN (
                SELECT etudiant_id FROM paiements
                WHERE annee_academique_id = %s AND school_id = %s
                GROUP BY etudiant_id
                HAVING SUM(montant) >= 60000
            )
        """, (year_id, tenant.current_school_id(), tenant.current_school_id(), year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT COUNT(DISTINCT etudiant_id) FROM paiements
            WHERE school_id = %s AND etudiant_id IN (
                SELECT id FROM users WHERE role = 'etudiant' AND school_id = %s
            ) AND etudiant_id IN (
                SELECT etudiant_id FROM paiements
                WHERE school_id = %s
                GROUP BY etudiant_id
                HAVING SUM(montant) >= 60000
            )
        """, (tenant.current_school_id(), tenant.current_school_id(), tenant.current_school_id()))
    etudiants_a_jour = cursor.fetchone()[0]

    # Répartition par filière (ne dépend pas de l'année)
    cursor.execute("""
        SELECT filiere, COUNT(*) as count
        FROM users
        WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != '' AND school_id = %s
        GROUP BY filiere
        ORDER BY count DESC
    """, (tenant.current_school_id(),))
    filieres_stats = cursor.fetchall()

    # Évolution mensuelle des paiements (6 derniers mois, filtrée par année)
    if year_id:
        cursor.execute("""
            SELECT
                DATE_FORMAT(date, '%%Y-%%m') as mois,
                SUM(montant) as total,
                COUNT(*) as nombre_paiements
            FROM paiements
            WHERE annee_academique_id = %s AND school_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
            GROUP BY DATE_FORMAT(date, '%%Y-%%m')
            ORDER BY mois DESC
            LIMIT 6
        """, (year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT
                DATE_FORMAT(date, '%%Y-%%m') as mois,
                SUM(montant) as total,
                COUNT(*) as nombre_paiements
            FROM paiements
            WHERE school_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
            GROUP BY DATE_FORMAT(date, '%%Y-%%m')
            ORDER BY mois DESC
            LIMIT 6
        """, (tenant.current_school_id(),))
    paiements_mensuels = cursor.fetchall()

    # Top 5 des dépenses récentes (filtrées par année)
    if year_id:
        cursor.execute("""
            SELECT description, ABS(montant) as montant, date
            FROM depenses
            WHERE annee_academique_id = %s AND school_id = %s
            ORDER BY ABS(montant) DESC, date DESC
            LIMIT 5
        """, (year_id, tenant.current_school_id()))
    else:
        cursor.execute("""
            SELECT description, ABS(montant) as montant, date
            FROM depenses
            WHERE school_id = %s
            ORDER BY ABS(montant) DESC, date DESC
            LIMIT 5
        """, (tenant.current_school_id(),))
    top_depenses = cursor.fetchall()
    
    conn.close()
    
    # Calculs de pourcentages et ratios
    taux_occupation_cours = round((courses_with_prof / count_courses * 100) if count_courses > 0 else 0, 1)
    taux_paiement = round((etudiants_a_jour / count_etudiants * 100) if count_etudiants > 0 else 0, 1)
    ratio_prof_etudiant = round((count_etudiants / count_professeurs) if count_professeurs > 0 else 0, 1)
    
    return render_template('admin_stats.html',
                         # Compteurs principaux
                         count_etudiants=count_etudiants,
                         count_professeurs=count_professeurs,
                         count_admins=count_admins,
                         count_courses=count_courses,
                         
                         # Finances
                         total_recettes=total_recettes,
                         total_depenses=total_depenses,
                         benefice_net=benefice_net,
                         
                         # Ratios et pourcentages
                         taux_occupation_cours=taux_occupation_cours,
                         taux_paiement=taux_paiement,
                         ratio_prof_etudiant=ratio_prof_etudiant,
                         etudiants_a_jour=etudiants_a_jour,
                         
                         # Données pour graphiques
                         filieres_stats=filieres_stats,
                         paiements_mensuels=paiements_mensuels,
                         top_depenses=top_depenses)

# API REST pour récupérer la liste au format JSON avec ID séquentiel
@app.route('/admin/api/etudiants')
@login_required
@admin_required
def api_etudiants():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT prenom, nom, email, filiere FROM users WHERE role='etudiant' AND school_id = %s ORDER BY nom ASC",
        (tenant.current_school_id(),)
    )
    rows = cursor.fetchall()
    conn.close()
    etudiants = []
    for i, row in enumerate(rows, start=1):
        etudiants.append({
            "id": i,
            "prenom": row["prenom"],
            "nom": row["nom"],
            "email": row["email"],
            "filiere": row["filiere"]
        })
    return jsonify(etudiants)

@app.route('/admin/filieres')
@login_required
@admin_required
def admin_filieres():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    cursor = conn.cursor(dictionary=True)
    
    # Vérifier si la colonne 'classe' existe
    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    classe_exists = cursor.fetchone() is not None
    
    # Construire la requête selon l'existence de la colonne classe
    if classe_exists:
        select_fields = "prenom, nom, email, filiere, niveau, classe"
    else:
        select_fields = "prenom, nom, email, filiere, niveau"
    
    query = f"""
        SELECT {select_fields}
        FROM users
        WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != ''
        AND niveau IS NOT NULL AND niveau != ''
        AND school_id = %s
        ORDER BY filiere, niveau, nom ASC
    """

    cursor.execute(query, (tenant.current_school_id(),))
    etudiants = cursor.fetchall()

    # Regrouper les étudiants par (filiere, niveau)
    # Convertir en liste de dictionnaires pour faciliter l'affichage dans le template
    groupes_list = []
    groupes_dict = {}
    for etu in etudiants:
        filiere = etu['filiere']
        niveau = etu['niveau']
        key = f"{filiere}_{niveau}"
        
        if key not in groupes_dict:
            groupes_dict[key] = {
                'filiere': filiere,
                'niveau': niveau,
                'etudiants': []
            }
        groupes_dict[key]['etudiants'].append(etu)
    
    # Convertir en liste triée
    groupes_list = sorted(groupes_dict.values(), key=lambda x: (x['filiere'], x['niveau']))

    conn.close()
    return render_template("admin_filieres.html", groupes=groupes_list, classe_exists=classe_exists)

@app.route('/admin/classes')
@login_required
@admin_required
def admin_classes():
    """Grille des classes basée sur les filières actives (Gestion filières & modules)."""
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    cursor = conn.cursor(dictionary=True)
    classes_par_filiere, filieres_actives = build_classes_par_filiere(cursor, tenant.current_school_id())
    conn.commit()
    conn.close()

    return render_template("admin_classes_grid.html",
                         classes_par_filiere=classes_par_filiere,
                         filieres_actives=filieres_actives,
                         mode='classes',
                         page_title='Gestion des Classes')

@app.route('/admin/class_students/<filiere>/<niveau>')
@login_required
@admin_required
def admin_class_students(filiere, niveau):
    """Affiche les étudiants d'une classe avec ou sans notes selon le mode"""
    from urllib.parse import unquote

    # Décoder les paramètres URL
    filiere = unquote(filiere)
    niveau = unquote(niveau)
    mode = request.args.get('mode', 'classes')  # 'notes' ou 'classes'
    
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_classes'))

    cursor = conn.cursor(dictionary=True)
    etudiants, filiere_row, nom_classe = get_students_for_class(cursor, filiere, niveau, school_id=tenant.current_school_id())
    if not filiere_row:
        conn.close()
        flash("Filière introuvable ou inactive.", "danger")
        return redirect(url_for('admin_classes'))
    filiere = filiere_row['nom']
    niveau = normaliser_niveau(niveau)
    conn.commit()

    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    classe_exists = cursor.fetchone() is not None

    # Si mode 'notes', récupérer aussi les notes
    notes_par_etudiant = {}
    semestre_exists = False
    visible_exists = False

    if mode == 'notes':
        # Vérifier si la colonne semestre existe
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'semestre'")
        semestre_exists = cursor.fetchone() is not None

        # Vérifier si la colonne visible existe
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
        visible_exists = cursor.fetchone() is not None

        # Filtrer par année académique
        year_id = get_current_year_id()

        # Récupérer toutes les notes pour les étudiants de cette classe
        etudiant_ids = [etu['id'] for etu in etudiants]

        if etudiant_ids:
            placeholders = ','.join(['%s'] * len(etudiant_ids))

            if semestre_exists and visible_exists:
                if year_id:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders}) AND annee_academique_id = %s
                    ''', tuple(etudiant_ids) + (year_id,))
                else:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders})
                    ''', tuple(etudiant_ids))
            elif semestre_exists:
                if year_id:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, 0 as visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders}) AND annee_academique_id = %s
                    ''', tuple(etudiant_ids) + (year_id,))
                else:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, 0 as visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders})
                    ''', tuple(etudiant_ids))
            elif visible_exists:
                if year_id:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders}) AND annee_academique_id = %s
                    ''', tuple(etudiant_ids) + (year_id,))
                else:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders})
                    ''', tuple(etudiant_ids))
            else:
                if year_id:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, 0 as visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders}) AND annee_academique_id = %s
                    ''', tuple(etudiant_ids) + (year_id,))
                else:
                    cursor.execute(f'''
                        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, 0 as visible
                        FROM notes
                        WHERE etudiant_id IN ({placeholders})
                    ''', tuple(etudiant_ids))

            notes_rows = cursor.fetchall()

            # Organiser les notes par étudiant
            for row in notes_rows:
                etu_id = row['etudiant_id']
                notes_par_etudiant.setdefault(etu_id, []).append(row)

    conn.close()

    # Choisir le template selon le mode
    if mode == 'notes':
        return render_template("admin_class_students_notes.html",
                             etudiants=etudiants,
                             notes_par_etudiant=notes_par_etudiant,
                             filiere=filiere,
                             niveau=niveau,
                             nom_classe=nom_classe,
                             semestre_exists=semestre_exists,
                             visible_exists=visible_exists)
    else:
        return render_template("admin_class_details.html",
                             etudiants=etudiants,
                             filiere=filiere,
                             niveau=niveau,
                             nom_classe=nom_classe,
                             classe_exists=classe_exists)

@app.route('/admin/classes/<filiere>/<niveau>')
@login_required
@admin_required
def admin_class_details(filiere, niveau):
    """Route de compatibilité - redirige vers admin_class_students en mode 'classes'"""
    return redirect(url_for('admin_class_students', filiere=filiere, niveau=niveau, mode='classes'))


@app.route('/admin/student/<int:etudiant_id>/credentials/print')
@login_required
@admin_required
def admin_student_credentials_print(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion.", "danger")
        return redirect(url_for('admin_classes'))
    cursor = conn.cursor(dictionary=True)
    ensure_student_account_columns(cursor)
    cursor.execute("""
        SELECT id, prenom, nom, email, identifiant, password_temp, filiere, niveau, classe, must_change_password
        FROM users WHERE id = %s AND role = 'etudiant' AND school_id = %s
    """, (etudiant_id, tenant.current_school_id()))
    student = cursor.fetchone()
    conn.close()
    if not student:
        flash("Étudiant introuvable.", "danger")
        return redirect(url_for('admin_classes'))
    return render_template('admin_student_credentials_print.html',
                           students=[student], single=True, nom_classe=student.get('classe', ''))


@app.route('/admin/class_students/<filiere>/<niveau>/credentials/print')
@login_required
@admin_required
def admin_class_credentials_print(filiere, niveau):
    from urllib.parse import unquote
    filiere = unquote(filiere)
    niveau = unquote(niveau)
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion.", "danger")
        return redirect(url_for('admin_classes'))
    cursor = conn.cursor(dictionary=True)
    ensure_student_account_columns(cursor)
    filiere_row = resolve_filiere_by_name(cursor, filiere, tenant.current_school_id())
    filiere_canon = filiere_row['nom'] if filiere_row else filiere
    filiere_code = (filiere_row or {}).get('code')
    niveau_norm = normaliser_niveau(niveau)
    nom_classe = generer_nom_classe(niveau_norm, filiere_canon, filiere_code)

    f_aliases = filiere_aliases(cursor, filiere_canon) or [filiere_canon]
    f_ph = ','.join(['%s'] * len(f_aliases))
    n_aliases = niveau_aliases(niveau) or [niveau_norm]
    n_ph = ','.join(['%s'] * len(n_aliases))

    params = [*f_aliases]
    filiere_clause = f"filiere IN ({f_ph})"
    if filiere_row:
        filiere_clause = f"(filiere_id = %s OR filiere IN ({f_ph}))"
        params = [filiere_row['id'], *f_aliases]

    cursor.execute(
        f"""
        SELECT id, prenom, nom, email, identifiant, password_temp, filiere, niveau, classe, must_change_password
        FROM users
        WHERE role = 'etudiant' AND {filiere_clause} AND niveau IN ({n_ph}) AND school_id = %s
        ORDER BY nom, prenom
        """,
        (*params, *n_aliases, tenant.current_school_id()),
    )
    students = cursor.fetchall()
    conn.close()
    return render_template('admin_student_credentials_print.html',
                           students=students, single=False,
                           nom_classe=nom_classe, filiere=filiere_canon, niveau=niveau_norm)


@app.route('/student/change-password', methods=['POST'])
@login_required
def student_change_password():
    if session.get('role') != 'etudiant':
        return jsonify({'success': False, 'message': 'Accès refusé'}), 403
    data = request.get_json() or request.form
    current = data.get('current_password', '')
    new_pass = data.get('new_password', '')
    confirm = data.get('confirm_password', new_pass)
    if len(new_pass) < 8:
        return jsonify({'success': False, 'message': 'Le mot de passe doit contenir au moins 8 caractères'}), 400
    if new_pass != confirm:
        return jsonify({'success': False, 'message': 'Les mots de passe ne correspondent pas'}), 400
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT password FROM users WHERE id = %s AND school_id = %s",
                   (session['user_id'], tenant.current_school_id()))
    user = cursor.fetchone()
    if not user or not check_password_hash(user['password'], current):
        conn.close()
        return jsonify({'success': False, 'message': 'Mot de passe actuel incorrect'}), 400
    cursor.execute("""
        UPDATE users SET password = %s, password_temp = NULL, must_change_password = 0
        WHERE id = %s AND school_id = %s
    """, (generate_password_hash(new_pass), session['user_id'], tenant.current_school_id()))
    conn.commit()
    conn.close()
    session.pop('must_change_password', None)
    return jsonify({'success': True, 'message': 'Mot de passe mis à jour'})


@app.route('/admin/api/professeurs')
@login_required
@admin_required
def api_professeurs():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT prenom, nom, email FROM users WHERE role='professeur' AND school_id = %s ORDER BY nom ASC",
        (tenant.current_school_id(),)
    )
    rows = cursor.fetchall()
    conn.close()
    profs = []
    for i, row in enumerate(rows, start=1):
        profs.append({
            "id": i,
            "prenom": row["prenom"],
            "nom": row["nom"],
            "email": row["email"],
        })
    return jsonify(profs)

@app.route('/admin/api/administrateurs')
@login_required
@admin_required
def api_admins():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT prenom, nom, email FROM users WHERE role='admin' AND school_id = %s ORDER BY nom ASC",
        (tenant.current_school_id(),)
    )
    rows = cursor.fetchall()
    conn.close()
    admins = []
    for i, row in enumerate(rows, start=1):
        admins.append({
            "id": i,
            "prenom": row["prenom"],
            "nom": row["nom"],
            "email": row["email"],
        })
    return jsonify(admins)


@app.route('/student/profile')
@login_required
def student_profile():
    if session.get('role') != 'etudiant':
        return redirect(url_for('admin_dashboard'))

    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('student_dashboard'))

    cursor = conn.cursor(dictionary=True)
    ensure_student_account_columns(cursor)
    conn.commit()
    cursor.execute("""
        SELECT prenom, nom, email, filiere, niveau, identifiant, must_change_password
        FROM users WHERE id = %s AND school_id = %s
    """, (user_id, tenant.current_school_id()))
    user_row = cursor.fetchone() or {}
    conn.close()

    return render_template('profile.html',
                           prenom=user_row.get('prenom', session.get('prenom', 'Étudiant')),
                           nom=user_row.get('nom', session.get('nom', '')),
                           email=user_row.get('email', session.get('user_email', 'non renseigné')),
                           filiere=user_row.get('filiere', session.get('filiere', '---')),
                           identifiant=user_row.get('identifiant', ''),
                           must_change_password=user_row.get('must_change_password', 0),
                           show_password_form=request.args.get('change_password') == '1' or user_row.get('must_change_password'),
                           photo_filename=session.get('photo_filename'))


# Route /student/chatbot deplacee vers routes/chatbot_student.py


def generer_qr_code_etudiant(user_id, student_data):
    """Générer un QR code unique pour un étudiant avec ses informations encodées"""
    # Créer un token unique et sécurisé pour l'étudiant
    timestamp = datetime.now().isoformat()
    data_to_encode = {
        'user_id': user_id,
        'nom': student_data['nom'],
        'prenom': student_data['prenom'],
        'filiere': student_data['filiere'],
        'niveau': student_data['niveau'],
        'type': 'student_presence',
        'timestamp': timestamp
    }

    # Créer une signature sécurisée
    data_string = json.dumps(data_to_encode, sort_keys=True)
    signature = hashlib.sha256(f"{data_string}ADSCLASS_SECRET_2024".encode()).hexdigest()[:16]

    # Ajouter la signature aux données
    data_to_encode['signature'] = signature

    # Encoder en JSON
    qr_data = json.dumps(data_to_encode)

    # Générer le QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)

    # Créer l'image
    img = qr.make_image(fill_color="black", back_color="white")

    # Convertir en base64 pour l'affichage HTML
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    return f"data:image/png;base64,{img_str}"


@app.route('/student/card')
@login_required
def student_card():
    if session.get('role') != 'etudiant':
        return redirect(url_for('admin_dashboard'))

    # Récupérer les informations de l'étudiant
    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('student_dashboard'))

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT prenom, nom, email, filiere, niveau, telephone
        FROM users
        WHERE id = %s AND school_id = %s
    """, (user_id, tenant.current_school_id()))
    student_data = cursor.fetchone()
    conn.close()

    if not student_data:
        flash("Données étudiant non trouvées.", "danger")
        return redirect(url_for('student_dashboard'))

    # Générer un ID étudiant unique basé sur l'ID utilisateur
    student_id = f"{user_id:06d}ADSCLASS"

    # Calculer la date d'expiration (2 ans à partir de maintenant)
    expiry_date = datetime.now() + timedelta(days=730)  # 2 ans

    # Générer le QR code
    qr_code_data = generer_qr_code_etudiant(user_id, student_data)

    return render_template('student_card.html',
                           student_data=student_data,
                           student_id=student_id,
                           expiry_date=expiry_date,
                           qr_code_data=qr_code_data)


@app.route('/delete_photo', methods=['POST'])
@login_required
def delete_photo():
    filename = session.get('photo_filename')
    if filename:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        session.pop('photo_filename', None)
        flash("Photo supprimée.", "info")
    return redirect(url_for('student_profile'))


@app.route('/student/factures')
@login_required
def student_factures():
    """Page des factures/paiements pour l'étudiant"""
    if session.get('role') != 'etudiant':
        flash("Accès refusé.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('student_dashboard'))

    cursor = conn.cursor(dictionary=True)

    # Récupérer les informations de l'étudiant
    cursor.execute("""
        SELECT id, prenom, nom, email, filiere, niveau, telephone
        FROM users
        WHERE id = %s
    """, (user_id,))
    etudiant = cursor.fetchone()

    if not etudiant:
        conn.close()
        flash("Données étudiant non trouvées.", "danger")
        return redirect(url_for('student_dashboard'))

    # Récupérer tous les paiements de l'étudiant
    cursor.execute("""
        SELECT id, date, montant, moyen, observation
        FROM paiements
        WHERE etudiant_id = %s
        ORDER BY date DESC
    """, (user_id,))
    paiements = cursor.fetchall()

    # Calculer les statistiques
    cursor.execute("""
        SELECT
            IFNULL(SUM(montant), 0) as total_paye,
            COUNT(*) as nombre_paiements
        FROM paiements
        WHERE etudiant_id = %s
    """, (user_id,))
    stats = cursor.fetchone()

    conn.close()

    # Montant dû (à adapter selon votre logique)
    montant_du = 60000
    total_paye = float(stats['total_paye']) if stats['total_paye'] else 0
    solde = montant_du - total_paye
    a_jour = total_paye >= montant_du

    return render_template('student_factures.html',
                           etudiant=etudiant,
                           paiements=paiements,
                           total_paye=total_paye,
                           montant_du=montant_du,
                           solde=solde,
                           a_jour=a_jour,
                           nombre_paiements=stats['nombre_paiements'])


@app.route('/student/facture/<int:paiement_id>/imprimer')
@login_required
def student_imprimer_facture(paiement_id):
    """Imprimer une facture professionnelle pour un paiement spécifique (côté étudiant)"""
    try:
        if session.get('role') != 'etudiant':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('student_factures'))

        cursor = conn.cursor(dictionary=True)

        # Récupérer le paiement et vérifier qu'il appartient à l'étudiant connecté
        cursor.execute("""
            SELECT p.*, u.prenom, u.nom, u.email, u.telephone, u.filiere, u.niveau
            FROM paiements p
            JOIN users u ON p.etudiant_id = u.id
            WHERE p.id = %s AND p.etudiant_id = %s
        """, (paiement_id, user_id))
        paiement = cursor.fetchone()
        conn.close()

        if not paiement:
            flash("Facture introuvable ou accès non autorisé.", "danger")
            return redirect(url_for('student_factures'))

        # Préparer les données pour le template
        from datetime import datetime
        import hashlib

        date_generation = datetime.now().strftime('%d/%m/%Y à %H:%M')

        # Fonction pour convertir montant en lettres (simplifiée)
        def nombre_en_lettres(montant):
            try:
                montant = float(montant) if montant else 0
                if montant == 0:
                    return "zéro franc CFA"
                elif montant < 1000:
                    return f"{int(montant)} francs CFA"
                else:
                    # Simplification pour les montants courants
                    milliers = int(montant // 1000)
                    reste = int(montant % 1000)
                    if reste == 0:
                        return f"{milliers} mille francs CFA"
                    else:
                        return f"{milliers} mille {reste} francs CFA"
            except:
                return "Montant non disponible"

        montant_en_lettres = nombre_en_lettres(paiement['montant'])

        # Code de sécurité unique
        security_string = f"{paiement_id}{paiement['montant']}{date_generation}"
        security_code = hashlib.md5(security_string.encode()).hexdigest()[:8].upper()

        # Utiliser le template professionnel existant
        return render_template('recu_paiement_pro.html',
                             paiement=paiement,
                             date_generation=date_generation,
                             montant_en_lettres=montant_en_lettres,
                             security_code=security_code,
                             type_recu='individuel')

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Erreur dans student_imprimer_facture: {error_details}")
        flash("Erreur lors de la génération de la facture.", "danger")
        return redirect(url_for('student_factures'))


def get_depense_by_id(id):
    conn = get_db_connection()
    if not conn:
        return None
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM depenses WHERE id = %s', (id,))
    depense = cursor.fetchone()
    conn.close()
    return depense

def update_depense(id, date, description, montant):
    conn = get_db_connection()
    if not conn:
        return False
    
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE depenses SET date = %s, description = %s, montant = %s WHERE id = %s AND school_id = %s',
        (date, description, montant, id, tenant.current_school_id())
    )
    conn.commit()
    conn.close()
    return True


@app.route('/admin/depenses/<int:id>/modifier', methods=['GET', 'POST'])
def modifier_depense(id):
    depense = get_depense_by_id(id)

    if not depense:
        return "Dépense non trouvée", 404

    if request.method == 'POST':
        date = request.form['date']
        description = request.form['description']
        montant = float(request.form['montant'])
        # Mise à jour dans la base de données (crée une fonction update_depense ou fais directement la requête)
        update_depense(id, date, description, -abs(montant))
        return redirect(url_for('admin_depenses'))

    # Affiche le montant en valeur absolue dans le formulaire
    depense_dict = depense  # depense est déjà un dict avec cursor(dictionary=True)
    depense_dict['montant'] = abs(depense_dict['montant'])
    return render_template('modifier_depense.html', depense=depense_dict)


@app.route('/admin/depenses/<int:id>/supprimer', methods=['POST', 'GET'])
def supprimer_depense(id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_depenses'))
    
    cursor = conn.cursor()
    cursor.execute('DELETE FROM depenses WHERE id = %s AND school_id = %s',
                   (id, tenant.current_school_id()))
    conn.commit()
    conn.close()
    flash("Dépense supprimée avec succès.", "success")
    return redirect(url_for('admin_depenses'))

# Route pour imprimer une dépense
@app.route('/admin/depenses/<int:id>/imprimer')
def imprimer_depense(id):
    depense = get_depense_by_id(id)
    if not depense:
        abort(404, description="Dépense non trouvée")
    now = datetime.now()
    return render_template('impression_depense.html', depense=depense, now=now)

def get_student_events(user_id):
    conn = get_db_connection()
    if not conn:
        return []
    
    cursor = conn.cursor(dictionary=True)

    # Récupérer la filière et le niveau de l'étudiant
    cursor.execute("SELECT filiere, niveau FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return []

    filiere = row["filiere"]
    niveau = row.get("niveau")
    
    # Vérifier si la colonne niveau existe dans courses
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    results = cursor.fetchall()
    niveau_exists = len(results) > 0
    
    # Récupérer les cours liés à cette filière et niveau (matching tolérant)
    f_aliases = filiere_aliases(cursor, filiere) or ([filiere] if filiere else [])
    n_aliases = niveau_aliases(niveau) if niveau_exists else []
    f_clause = (
        f"c.filiere IN ({','.join(['%s'] * len(f_aliases))})"
        if f_aliases else "1=1"
    )
    if n_aliases:
        n_clause = (
            f"AND (c.niveau IN ({','.join(['%s'] * len(n_aliases))}) "
            f"OR c.niveau IS NULL OR c.niveau = '')"
        )
        _et_join = student_enrollment_join_sql('c', 'et')
        _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')
        cursor.execute(f"""
            SELECT c.id, c.nom_cours AS title, c.start, c.end
            FROM courses c
            {_et_join}
            WHERE et.user_id = %s AND et.role = 'etudiant'
            AND {f_clause}
            {n_clause}
            {_tenant_w}
            ORDER BY c.start
        """, (user_id, *f_aliases, *n_aliases, *_tenant_p))
    else:
        _et_join = student_enrollment_join_sql('c', 'et')
        _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')
        cursor.execute(f"""
            SELECT c.id, c.nom_cours AS title, c.start, c.end
            FROM courses c
            {_et_join}
            WHERE et.user_id = %s AND et.role = 'etudiant'
            AND {f_clause}
            {_tenant_w}
            ORDER BY c.start
        """, (user_id, *f_aliases, *_tenant_p))
    
    events = cursor.fetchall()
    conn.close()

    return events

def get_student_notes(user_id, cours_list, school_id=None):
    conn = get_db_connection()
    if not conn:
        return {}
    
    cursor = conn.cursor(dictionary=True)
    sid = school_id if school_id is not None else tenant.current_school_id()

    notes = {}
    for cours in cours_list:
        cursor.execute('''
            SELECT cc1, cc2, participation, examen FROM notes 
            WHERE etudiant_id = %s AND nom_cours = %s AND school_id = %s
        ''', (user_id, cours, sid))
        result = cursor.fetchone()
        if result:
            notes[cours] = {
                'cc1': result['cc1'],
                'cc2': result['cc2'],
                'participation': result['participation'],
                'examen': result['examen'],
            }
        else:
            notes[cours] = {
                'cc1': None,
                'cc2': None,
                'participation': None,
                'examen': None,
            }
    conn.close()
    return notes


@app.route('/student/courses')
@login_required
def student_courses():
    """Afficher tous les modules de l'étudiant sans doublons"""
    user_id = session['user_id']
    user_filiere = session.get('filiere', 'IAM')
    
    if session.get('role') != 'etudiant':
        flash("Accès refusé.", "danger")
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('student_dashboard'))
    
    cursor = conn.cursor(dictionary=True)

    user_niveau = session.get('niveau')

    f_aliases = filiere_aliases(cursor, user_filiere) or ([user_filiere] if user_filiere else [])
    n_aliases = niveau_aliases(user_niveau)
    f_clause = (
        f"c.filiere IN ({','.join(['%s'] * len(f_aliases))})"
        if f_aliases else "1=1"
    )
    if n_aliases:
        n_clause = (
            f"AND (c.niveau IN ({','.join(['%s'] * len(n_aliases))}) "
            f"OR c.niveau IS NULL OR c.niveau = '')"
        )
    else:
        n_clause = ""

    _et_join = student_enrollment_join_sql('c', 'et')
    _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')

    cursor.execute(f"""
        SELECT
            c.id,
            c.nom_cours,
            c.filiere,
            c.niveau,
            c.description,
            c.salle,
            c.heure_debut,
            c.heure_fin,
            c.jour_semaine,
            c.date_cours,
            c.start,
            c.end
        FROM courses c
        {_et_join}
        WHERE et.user_id = %s AND et.role = 'etudiant' AND {f_clause}
        {n_clause}
        {_tenant_w}
        ORDER BY c.start, c.nom_cours
    """, (user_id, *f_aliases, *n_aliases, *_tenant_p))
    
    cours = cursor.fetchall()
    conn.close()
    
    # Récupérer les notes pour ces cours
    cours_noms = [c['nom_cours'] for c in cours]
    notes = get_student_notes(user_id, cours_noms, tenant.current_school_id())
    
    return render_template('student_courses.html', cours=cours, notes=notes, filiere=user_filiere)

@app.route('/student/course/<int:course_id>/manage')
@login_required
def student_course_manage(course_id):
    """Page de détail d'un module pour l'étudiant (lecture seule)"""
    try:
        if session.get('role') != 'etudiant':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('student_courses'))

        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que l'étudiant est inscrit à ce cours (course_id + school_id)
        _et_join = student_enrollment_join_sql('c', 'et')
        _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')
        cursor.execute(f"""
            SELECT c.*, 
                   prof.prenom as prof_prenom, 
                   prof.nom as prof_nom
            FROM courses c
            {_et_join}
            LEFT JOIN emploi_temps et_prof ON c.id = et_prof.course_id AND et_prof.role = 'professeur'
            LEFT JOIN users prof ON et_prof.user_id = prof.id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'etudiant'
            {_tenant_w}
            LIMIT 1
        """, (course_id, user_id, *_tenant_p))
        
        course = cursor.fetchone()
        
        if not course:
            flash("Module non trouvé ou accès refusé.", "danger")
            conn.close()
            return redirect(url_for('student_courses'))

        # Récupérer les présences de cet étudiant pour ce cours
        year_id = get_current_year_id()
        if year_id:
            cursor.execute("""
                SELECT date_cours, statut, commentaire
                FROM presences
                WHERE course_id = %s AND etudiant_id = %s AND annee_academique_id = %s
                ORDER BY date_cours DESC
            """, (course_id, user_id, year_id))
        else:
            cursor.execute("""
                SELECT date_cours, statut, commentaire
                FROM presences
                WHERE course_id = %s AND etudiant_id = %s
                ORDER BY date_cours DESC
            """, (course_id, user_id))
        presences = cursor.fetchall()

        # Récupérer les documents/ressources visibles pour ce cours (par course_id)
        cursor.execute("""
            SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload
            FROM documents
            WHERE course_id = %s AND visible = 1 AND school_id = %s
            AND date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
            ORDER BY date_upload DESC
        """, (course_id, tenant.current_school_id()))
        documents = cursor.fetchall()

        # Récupérer les lectures
        cursor.execute("""
            SELECT id, titre, description, contenu, date_seance, ordre
            FROM lectures
            WHERE course_id = %s AND school_id = %s
            ORDER BY ordre, date_seance
        """, (course_id, tenant.current_school_id()))
        lectures = cursor.fetchall()

        # Récupérer les examens
        cursor.execute("""
            SELECT id, type_examen, titre, date_examen, coefficient, description
            FROM exams
            WHERE course_id = %s AND school_id = %s
            ORDER BY date_examen
        """, (course_id, tenant.current_school_id()))
        exams = cursor.fetchall()

        # Récupérer les assignments
        cursor.execute("""
            SELECT id, titre, description, date_publication, date_limite, type_assignment, fichier_corrige
            FROM assignments
            WHERE course_id = %s AND school_id = %s
            ORDER BY date_publication DESC
        """, (course_id, tenant.current_school_id()))
        assignments = cursor.fetchall()

        # Récupérer les notes du gradebook pour cet étudiant
        cursor.execute("""
            SELECT type_note, note, coefficient, date_note, commentaire
            FROM gradebook
            WHERE course_id = %s AND etudiant_id = %s
            ORDER BY date_note DESC
        """, (course_id, user_id))
        gradebook_notes = cursor.fetchall()

        # Récupérer les notes de la table notes (si visible)
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
        visible_exists = cursor.fetchone() is not None
        
        notes_data = None
        if visible_exists:
            cursor.execute("""
                SELECT CC1, CC2, Participation, Examen
                FROM notes
                WHERE etudiant_id = %s AND nom_cours = %s AND visible = 1 AND school_id = %s
            """, (user_id, course['nom_cours'], tenant.current_school_id()))
            notes_data = cursor.fetchone()

        conn.close()

        return render_template('student_course_manage.html',
                              course=course,
                              presences=presences,
                              documents=documents,
                              lectures=lectures,
                              exams=exams,
                              assignments=assignments,
                              gradebook_notes=gradebook_notes,
                              notes_data=notes_data,
                              nom=session.get('nom', ''),
                              prenom=session.get('prenom', ''))

    except Exception as e:
        import traceback
        error_msg = f"Erreur chargement module étudiant: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash(f"Erreur lors du chargement du module: {str(e)}", "error")
        return redirect(url_for('student_courses'))


# Route student_absences supprimée - remplacée par la nouvelle version


# (routes notes/examens déplacées vers routes/grades.py)

 # Route: Gestion des absences (supprimée - remplacée par la nouvelle version)


@app.route('/student_grades')
@login_required
def student_grades():
    user_id = session['user_id']  # L'étudiant connecté
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('student_dashboard'))
    
    cursor = conn.cursor(dictionary=True)

    # Vérifier si la colonne visible existe
    cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
    visible_exists = cursor.fetchone() is not None

    # Filtrer par année académique et école
    year_id = get_current_year_id()
    year_filter = "AND annee_academique_id = %s" if year_id else ""
    sid = tenant.current_school_id()
    school_filter = "AND school_id = %s"

    # Récupération des cours liés à cet étudiant (uniquement les notes visibles)
    if visible_exists:
        if year_id:
            cursor.execute(f"SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s AND visible = 1 {school_filter} AND annee_academique_id = %s", (user_id, sid, year_id))
        else:
            cursor.execute(f"SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s AND visible = 1 {school_filter}", (user_id, sid))
    else:
        if year_id:
            cursor.execute(f"SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s {school_filter} AND annee_academique_id = %s", (user_id, sid, year_id))
        else:
            cursor.execute(f"SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s {school_filter}", (user_id, sid))
    cours = [row['nom_cours'] for row in cursor.fetchall()]

    notes_dict = {}
    for nom_cours in cours:
        if visible_exists:
            if year_id:
                cursor.execute("""
                    SELECT CC1, CC2, Participation, Examen
                    FROM notes
                    WHERE etudiant_id = %s AND nom_cours = %s AND visible = 1
                    AND school_id = %s AND annee_academique_id = %s
                """, (user_id, nom_cours, sid, year_id))
            else:
                cursor.execute("""
                    SELECT CC1, CC2, Participation, Examen
                    FROM notes
                    WHERE etudiant_id = %s AND nom_cours = %s AND visible = 1 AND school_id = %s
                """, (user_id, nom_cours, sid))
        else:
            if year_id:
                cursor.execute("""
                    SELECT CC1, CC2, Participation, Examen
                    FROM notes
                    WHERE etudiant_id = %s AND nom_cours = %s AND school_id = %s AND annee_academique_id = %s
                """, (user_id, nom_cours, sid, year_id))
            else:
                cursor.execute("""
                    SELECT CC1, CC2, Participation, Examen
                    FROM notes
                    WHERE etudiant_id = %s AND nom_cours = %s AND school_id = %s
                """, (user_id, nom_cours, sid))
        note = cursor.fetchone()

        if note:
            moyenne = None
            if all(note[k] is not None for k in ['CC1', 'CC2', 'Participation', 'Examen']):
                moyenne = round((note['CC1'] + note['CC2'] + note['Participation'] + note['Examen']) / 4, 2)

            notes_dict[nom_cours] = {
                'CC1': note['CC1'],
                'CC2': note['CC2'],
                'Participation': note['Participation'],
                'Examen': note['Examen'],
                'Moyenne Finale': moyenne
            }

    conn.close()

    return render_template('student_grades.html', cours=cours, notes=notes_dict)


# Route de test simple pour vérifier les filtres
@app.route('/admin/test/recu')
@login_required
def test_recu():
    try:
        # Données de test
        test_data = {
            'id': 1,
            'prenom': 'Jean',
            'nom': 'Dupont',
            'email': 'jean.dupont@example.com',
            'telephone': '+221 77 123 45 67',
            'montant': 50000,
            'date': '2026-08-05',
            'moyen': 'Espèces',
            'observation': 'Paiement de test',
            'etudiant_id': 1
        }

        from datetime import datetime
        import hashlib

        date_generation = datetime.now().strftime('%d/%m/%Y à %H:%M')
        security_string = f"1{test_data['montant']}{date_generation}"
        security_code = hashlib.md5(security_string.encode()).hexdigest()[:8].upper()

        return render_template('recu_paiement_pro.html',
                             paiement=test_data,
                             date_generation=date_generation,
                             montant_en_lettres="Cinquante mille francs CFA",
                             security_code=security_code,
                             type_recu='individuel')

    except Exception as e:
        return f"Erreur de test: {str(e)}", 500

# Route de diagnostic pour vérifier les paiements
@app.route('/admin/debug/paiement/<int:paiement_id>')
@login_required
def debug_paiement(paiement_id):
    try:
        if session.get('role') != 'admin':
            return "Accès refusé", 403

        conn = get_db_connection()
        if not conn:
            return f"<h1>Erreur de connexion à la base de données</h1>"

        cursor = conn.cursor(dictionary=True)
        # Vérifier si le paiement existe
        cursor.execute("SELECT * FROM paiements WHERE id = %s", (paiement_id,))
        paiement = cursor.fetchone()

        if not paiement:
            conn.close()
            return f"<h1>Paiement ID {paiement_id} non trouvé</h1>"

        # Vérifier l'étudiant associé
        cursor.execute("SELECT * FROM users WHERE id = %s AND school_id = %s", (paiement['etudiant_id'], tenant.current_school_id()))
        etudiant = cursor.fetchone()

        # Vérifier la jointure (ancienne méthode)
        try:
            cursor.execute(
                "SELECT p.*, u.prenom, u.nom, u.email, u.telephone FROM paiements p JOIN users u ON p.etudiant_id = u.id WHERE p.id = %s",
                (paiement_id,)
            )
            jointure = cursor.fetchone()
            jointure_result = jointure if jointure else 'Échec de la jointure'
        except Exception as join_error:
            jointure_result = f"Erreur jointure: {str(join_error)}"

        conn.close()

        # Afficher les informations de debug
        debug_info = f"""
        <h1>Debug Paiement ID {paiement_id}</h1>

        <h2>✅ Paiement trouvé:</h2>
        <pre>{paiement if paiement else 'Aucun'}</pre>

        <h2>✅ Étudiant associé (ID {paiement['etudiant_id'] if paiement else 'N/A'}):</h2>
        <pre>{etudiant if etudiant else 'Aucun - PROBLÈME ICI!'}</pre>

        <h2>❓ Jointure (ancienne méthode):</h2>
        <pre>{jointure_result}</pre>

        <h2>🔧 Nouvelle Approche (séparée):</h2>
        <p>Le reçu individuel utilise maintenant la même approche que le reçu annuel qui fonctionne :</p>
        <ul>
            <li>1. Récupérer le paiement séparément</li>
            <li>2. Récupérer l'étudiant séparément</li>
            <li>3. Combiner les données manuellement</li>
        </ul>

        <h2>🚀 Actions de Test:</h2>
        <a href="/admin/etudiant/paiement/{paiement_id}/recu/pro" style="display:inline-block; padding:10px; background:#007bff; color:white; text-decoration:none; margin:5px;">🧾 Tester le Reçu Individuel (Nouvelle Version)</a><br>
        <a href="/admin/etudiant/{paiement['etudiant_id'] if paiement else 1}/recu/annuel" style="display:inline-block; padding:10px; background:#28a745; color:white; text-decoration:none; margin:5px;">📊 Tester le Reçu Annuel (Qui Fonctionne)</a><br>
        <a href="/admin/test/recu" style="display:inline-block; padding:10px; background:#6c757d; color:white; text-decoration:none; margin:5px;">🧪 Tester avec Données Fictives</a>

        <h2>💡 Diagnostic:</h2>
        <p><strong>Problème probable :</strong> {'✅ Données OK' if paiement and etudiant else '❌ Données manquantes'}</p>
        <p><strong>Jointure :</strong> {'✅ Fonctionne' if 'Erreur' not in str(jointure_result) else '❌ Problème de jointure'}</p>
        <p><strong>Solution :</strong> Nouvelle approche sans jointure, comme le reçu annuel</p>
        """

        return debug_info

    except Exception as e:
        import traceback
        return f"<h1>Erreur de debug:</h1><pre>{traceback.format_exc()}</pre>"

# Route de debug pour vérifier les données du professeur
@app.route('/admin/debug/professeur/<int:prof_id>')
@login_required
def debug_professeur(prof_id):
    try:
        if session.get('role') != 'admin':
            return "Accès refusé", 403

        conn = get_db_connection()
        if not conn:
            return f"<h1>Erreur de connexion à la base de données</h1>"

        cursor = conn.cursor(dictionary=True)
        # Vérifier le professeur
        cursor.execute("SELECT * FROM users WHERE id = %s AND role = 'professeur' AND school_id = %s", (prof_id, tenant.current_school_id()))
        professeur = cursor.fetchone()

        if not professeur:
            conn.close()
            return f"<h1>Professeur ID {prof_id} non trouvé</h1>"

        # Vérifier ses cours
        cursor.execute("SELECT * FROM courses WHERE professeur_id = %s", (prof_id,))
        cours_assignes = cursor.fetchall()

        # Vérifier son emploi du temps
        cursor.execute(
            "SELECT et.*, c.nom_cours FROM emploi_temps et JOIN courses c ON et.course_id = c.id WHERE et.user_id = %s AND et.role = 'professeur'",
            (prof_id,)
        )
        emploi_temps = cursor.fetchall()

        conn.close()

        # Afficher les informations de debug
        debug_info = f"""
        <h1>Debug Professeur ID {prof_id}</h1>

        <h2>✅ Professeur trouvé:</h2>
        <pre>{professeur if professeur else 'Aucun'}</pre>

        <h2>📚 Cours assignés directement:</h2>
        <pre>{cours_assignes if cours_assignes else 'Aucun cours assigné'}</pre>

        <h2>📅 Emploi du temps:</h2>
        <pre>{emploi_temps if emploi_temps else 'Aucun dans emploi_temps'}</pre>

        <h2>🔧 Actions:</h2>
        <a href="/professeur/dashboard" style="display:inline-block; padding:10px; background:#007bff; color:white; text-decoration:none; margin:5px;">🎯 Tester Dashboard Professeur</a><br>
        <a href="/admin/add_course" style="display:inline-block; padding:10px; background:#28a745; color:white; text-decoration:none; margin:5px;">➕ Ajouter un Cours</a><br>

        <h2>💡 Diagnostic:</h2>
        <p><strong>Professeur :</strong> {'✅ Existe' if professeur else '❌ Introuvable'}</p>
        <p><strong>Cours assignés :</strong> {'✅ ' + str(len(cours_assignes)) + ' cours' if cours_assignes else '❌ Aucun cours'}</p>
        <p><strong>Emploi du temps :</strong> {'✅ ' + str(len(emploi_temps)) + ' entrées' if emploi_temps else '❌ Vide'}</p>

        <h2>🚀 Solution:</h2>
        <p>Si aucun cours n'est assigné, utilisez le formulaire d'ajout de cours et sélectionnez ce professeur.</p>
        """

        return debug_info

    except Exception as e:
        import traceback
        return f"<h1>Erreur de debug professeur:</h1><pre>{traceback.format_exc()}</pre>"

# Route de test ultra-simple pour le dashboard professeur
@app.route('/professeur/test-dashboard')
@login_required
def test_professeur_emploi_temps():
    try:
        if session.get('role') != 'professeur':
            return "Accès refusé - pas professeur", 403

        user_id = session['user_id']

        # Test de base
        html = f"""
        <h1>🧪 Test Dashboard Professeur</h1>
        <p><strong>User ID:</strong> {user_id}</p>
        <p><strong>Nom:</strong> {session.get('nom', 'Non défini')}</p>
        <p><strong>Prénom:</strong> {session.get('prenom', 'Non défini')}</p>
        <p><strong>Role:</strong> {session.get('role', 'Non défini')}</p>

        <h2>🔍 Test Base de Données</h2>
        """

        # Test connexion DB
        try:
            conn = get_db_connection()
            if not conn:
                html += "<p>❌ Connexion DB: Échec</p>"
            else:
                html += "<p>✅ Connexion DB: OK</p>"

                # Test requête cours
                try:
                    cursor = conn.cursor(dictionary=True)
                    courses_query = """
                        SELECT c.*, et.visible, et.notifications
                        FROM courses c
                        JOIN emploi_temps et ON c.id = et.course_id
                        WHERE et.user_id = %s AND et.role = 'professeur'
                    """
                    cursor.execute(courses_query, (user_id,))
                    cours = cursor.fetchall()
                    html += f"<p>✅ Requête cours: {len(cours)} cours trouvés</p>"

                    for i, course in enumerate(cours):
                        html += f"<p>📚 Cours {i+1}: {course.get('nom_cours', 'Sans nom')} - Filière: {course.get('filiere', 'Non définie')}</p>"

                except Exception as e:
                    html += f"<p>❌ Erreur requête cours: {str(e)}</p>"

                conn.close()

        except Exception as e:
            html += f"<p>❌ Erreur connexion DB: {str(e)}</p>"

        html += f"""
        <h2>🚀 Actions</h2>
        <a href="/professeur/dashboard" style="display:inline-block; padding:10px; background:#007bff; color:white; text-decoration:none; margin:5px;">🎯 Tester Dashboard Normal</a><br>
        <a href="/admin/add_course" style="display:inline-block; padding:10px; background:#28a745; color:white; text-decoration:none; margin:5px;">➕ Ajouter un Cours</a><br>
        <a href="/logout" style="display:inline-block; padding:10px; background:#dc3545; color:white; text-decoration:none; margin:5px;">🚪 Déconnexion</a>
        """

        return html

    except Exception as e:
        import traceback
        return f"<h1>Erreur test dashboard:</h1><pre>{traceback.format_exc()}</pre>"


# Route pour uploader un document
@app.route('/professeur/cours/<int:course_id>/upload', methods=['GET', 'POST'])
@login_required
def upload_document(course_id):
    if session.get('role') != 'professeur':
        return redirect(url_for('login'))

    if request.method == 'GET':
        # Afficher la page d'upload
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données', 'error')
            return redirect(url_for('professeur_emploi_temps'))
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM courses WHERE id = %s AND professeur_id = %s',
                      (course_id, session['user_id']))
        course = cursor.fetchone()
        conn.close()

        if not course:
            flash('Cours non trouvé', 'error')
            return redirect(url_for('professeur_emploi_temps'))

        return render_template('professeur_upload_document.html',
                             course=course,
                             nom=session.get('nom'),
                             prenom=session.get('prenom'))

    # POST - Traitement de l'upload
    if 'document' not in request.files:
        flash('Aucun fichier sélectionné', 'error')
        return redirect(request.url)

    file = request.files['document']
    if file.filename == '':
        flash('Aucun fichier sélectionné', 'error')
        return redirect(request.url)

    if file:
        try:
            # Créer le dossier uploads s'il n'existe pas
            upload_folder = 'uploads'
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)

            # Sécuriser le nom de fichier
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename

            # Chemin complet du fichier
            filepath = os.path.join(upload_folder, filename)
            file.save(filepath)

            # Informations du fichier
            file_size = os.path.getsize(filepath)
            file_extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

            # Enregistrer en base de données 
            conn = get_db_connection()
            if not conn:
                flash('Erreur de connexion à la base de données', 'error')
                return redirect(request.url)
            
            cursor = conn.cursor(dictionary=True)
            
            # Récupérer le nom_cours et la filiere du cours
            cursor.execute("SELECT nom_cours, filiere FROM courses WHERE id = %s", (course_id,))
            course_info = cursor.fetchone()
            
            nom_cours = course_info['nom_cours'] if course_info else None
            filiere_cours = course_info['filiere'] if course_info else None
            
            # Vérifier si les colonnes nom_cours et filiere existent dans documents
            cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
            nom_cours_exists = cursor.fetchone() is not None
            
            if not nom_cours_exists:
                # Ajouter les colonnes si elles n'existent pas
                try:
                    cursor.execute("ALTER TABLE documents ADD COLUMN nom_cours VARCHAR(255) AFTER course_id")
                    cursor.execute("ALTER TABLE documents ADD COLUMN filiere VARCHAR(255) AFTER nom_cours")
                    conn.commit()
                except Exception as e:
                    print(f"Erreur ajout colonnes nom_cours/filiere: {e}")
            
            # Insérer le document avec nom_cours et filiere si disponibles
            if nom_cours_exists and nom_cours and filiere_cours:
                cursor.execute('''
                INSERT INTO documents
                (course_id, nom_cours, filiere, professeur_id, titre, description, nom_fichier, chemin_fichier,
                 taille_fichier, type_fichier, visible, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (course_id, nom_cours, filiere_cours, session['user_id'],
                      request.form.get('titre', filename),
                      request.form.get('description', ''),
                      filename, filepath, file_size, file_extension, 1, tenant.current_school_id()))
            else:
                # Compatibilité avec l'ancienne structure
                cursor.execute('''
                INSERT INTO documents
                (course_id, professeur_id, titre, description, nom_fichier, chemin_fichier,
                 taille_fichier, type_fichier, visible, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (course_id, session['user_id'],
                      request.form.get('titre', filename),
                      request.form.get('description', ''),
                      filename, filepath, file_size, file_extension, 1, tenant.current_school_id()))

            conn.commit()
            conn.close()

            flash('Document uploadé avec succès!', 'success')
            return redirect(url_for('prof_classes'))

        except Exception as e:
            flash(f'Erreur lors de l\'upload: {str(e)}', 'error')
            return redirect(request.url)

# Route pour télécharger un document
@app.route('/download/<int:document_id>')
@login_required
def download_document(document_id):
    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données', 'error')
        return redirect(url_for('student_dashboard'))
    
    cursor = conn.cursor(dictionary=True)

    try:
        # Récupérer le document
        cursor.execute('''
        SELECT d.*, c.nom_cours
        FROM documents d
        JOIN courses c ON d.course_id = c.id
        WHERE d.id = %s AND d.visible = 1 AND d.school_id = %s
        ''', (document_id, tenant.current_school_id()))

        document = cursor.fetchone()
        if not document:
            flash('Document non trouvé', 'error')
            return redirect(url_for('student_dashboard'))

        # Vérifier que l'utilisateur a accès au cours
        if session.get('role') == 'etudiant':
            cursor.execute('''
            SELECT 1 FROM emploi_temps
            WHERE user_id = %s AND course_id = %s AND role = 'etudiant'
            ''', (session['user_id'], document['course_id']))

            if not cursor.fetchone():
                flash('Accès non autorisé à ce document', 'error')
                return redirect(url_for('student_dashboard'))

        elif session.get('role') == 'professeur':
            # Le professeur peut télécharger ses propres documents
            if document['professeur_id'] != session['user_id']:
                flash('Accès non autorisé à ce document', 'error')
                return redirect(url_for('professeur_emploi_temps'))

        conn.close()

        # Télécharger le fichier
        if os.path.exists(document['chemin_fichier']):
            return send_file(document['chemin_fichier'],
                           as_attachment=True,
                           download_name=document['nom_fichier'])
        else:
            flash('Fichier non trouvé sur le serveur', 'error')
            return redirect(url_for('student_dashboard'))

    except Exception as e:
        conn.close()
        flash(f'Erreur lors du téléchargement: {str(e)}', 'error')
        return redirect(url_for('student_dashboard'))

# Route pour voir les documents d'un cours (étudiant)
@app.route('/cours/<int:course_id>/documents')
@login_required
def voir_documents_cours(course_id):
    if session.get('role') != 'etudiant':
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données', 'error')
        return redirect(url_for('student_dashboard'))
    
    cursor = conn.cursor(dictionary=True)

    try:
        # Vérifier que l'étudiant est inscrit au cours (course_id + school_id)
        _et_join = student_enrollment_join_sql('c', 'et')
        _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')
        cursor.execute(f'''
        SELECT c.* FROM courses c
        {_et_join}
        WHERE c.id = %s AND et.user_id = %s AND et.role = 'etudiant'
        {_tenant_w}
        ''', (course_id, session['user_id'], *_tenant_p))

        course = cursor.fetchone()
        if not course:
            flash('Cours non trouvé ou accès non autorisé', 'error')
            return redirect(url_for('student_dashboard'))

        # Récupérer tous les documents du cours (par course_id)
        cursor.execute('''
        SELECT d.*, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN users u ON d.professeur_id = u.id
        WHERE d.course_id = %s AND d.visible = 1 AND d.school_id = %s
        ORDER BY d.date_upload DESC
        ''', (course_id, tenant.current_school_id()))

        documents = cursor.fetchall()

        conn.close()

        return render_template('etudiant_documents_cours.html',
                             course=course,
                             documents=documents,
                             nom=session.get('nom'),
                             prenom=session.get('prenom'))

    except Exception as e:
        conn.close()
        flash(f'Erreur lors du chargement des documents: {str(e)}', 'error')
        return redirect(url_for('student_dashboard'))

# Route pour l'ajout de cours ultra-simple
@app.route('/admin/ajouter-cours-simple', methods=['GET', 'POST'])
@login_required
def admin_ajouter_cours_simple():
    if session.get('role') != 'admin':
        flash('Accès non autorisé', 'error')
        return redirect(url_for('login'))

    if request.method == 'GET':
        # Récupérer la liste des professeurs et des filières canoniques
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données', 'error')
            return redirect(url_for('admin_dashboard'))

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, nom, prenom FROM users WHERE role = 'professeur' AND school_id = %s ORDER BY nom, prenom", (tenant.current_school_id(),))
        professeurs = cursor.fetchall()
        filieres = get_canonical_active_filieres(cursor, tenant.current_school_id())
        conn.close()

        niveaux = [
            ('Licence 1', 'Licence 1'),
            ('Licence 2', 'Licence 2'),
            ('Licence 3', 'Licence 3'),
            ('Master 1', 'Master 1'),
            ('Master 2', 'Master 2'),
        ]
        return render_template(
            'admin_ajouter_cours_simple.html',
            professeurs=professeurs,
            filieres=filieres,
            niveaux=niveaux,
        )

    # POST - Traitement du formulaire
    try:
        # Récupérer les données du formulaire
        nom_cours = request.form.get('nom_cours')
        filiere = request.form.get('filiere')
        niveau = request.form.get('niveau', '').strip()  # Ajout récupération niveau
        professeur_nom = request.form.get('professeur_nom', '').strip()
        salle = request.form.get('salle', '')
        date_cours = request.form.get('date_cours')
        jour_semaine = request.form.get('jour_semaine')
        heure_debut = request.form.get('heure_debut')
        heure_fin = request.form.get('heure_fin')
        start = request.form.get('start')
        end = request.form.get('end')

        # Validation
        if not all([nom_cours, filiere, date_cours, jour_semaine, heure_debut, heure_fin, start, end]):
            flash('Veuillez remplir tous les champs obligatoires', 'error')
            return redirect(request.url)

        # Une seule connexion pour toute la transaction
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données', 'error')
            return redirect(request.url)

        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()

        # Recherche du professeur par nom (si fourni)
        professeur_id = None
        if professeur_nom:
            cursor.execute("""
                SELECT id FROM users
                WHERE role = 'professeur'
                AND (LOWER(CONCAT(nom, ' ', prenom)) LIKE LOWER(%s)
                     OR LOWER(CONCAT(prenom, ' ', nom)) LIKE LOWER(%s))
                AND school_id = %s
                LIMIT 1
            """, (f'%{professeur_nom}%', f'%{professeur_nom}%', tenant.current_school_id()))
            prof = cursor.fetchone()
            if prof:
                professeur_id = prof['id']

        # Forcer la forme canonique (filière de la table filieres, niveau long)
        filiere_row = resolve_filiere_by_name(cursor, filiere, tenant.current_school_id())
        if not filiere_row:
            conn.close()
            flash(
                "Filière invalide : sélectionnez une filière créée dans Gestion des filières & modules.",
                'error'
            )
            return redirect(request.url)
        filiere_canon = filiere_row['nom']
        niveau_short = normaliser_niveau(niveau, fallback='') if niveau else ''
        niveau_canon = NIVEAU_SHORT_TO_LONG.get(niveau_short, niveau or '')

        cursor.execute('''
            INSERT INTO courses (nom_cours, professeur_id, professeur_nom, start, end, filiere, niveau, salle,
            date_cours, jour_semaine, heure_debut, heure_fin, recurrent, description, annee_academique_id, school_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (nom_cours, professeur_id, professeur_nom, start, end, filiere_canon, niveau_canon, salle,
            date_cours, jour_semaine, heure_debut, heure_fin, 1,
            f"Cours de {nom_cours} en {filiere_canon}", year_id, tenant.current_school_id()))

        course_id = cursor.lastrowid

        # Ajouter automatiquement le professeur à l'emploi du temps s'il est trouvé dans la base
        if professeur_id:
            cursor.execute('''
            INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications, school_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ''', (professeur_id, course_id, 'professeur', 1, 1, tenant.current_school_id()))

        # Inscription automatique (événement métier centralisé) : tous les
        # étudiants de la même école / filière / niveau sont inscrits au cours.
        nb_etudiants_inscrits = sync_enrollments_for_course(cursor, course_id, tenant.current_school_id())

        conn.commit()
        conn.close()

        # Message de succès personnalisé
        message = f'Cours "{nom_cours}" créé avec succès pour le {jour_semaine} {date_cours} de {heure_debut} à {heure_fin}!'
        if professeur_nom:
            if professeur_id:
                message += f' Professeur "{professeur_nom}" trouvé et ajouté automatiquement.'
            else:
                message += f' Professeur "{professeur_nom}" enregistré (non trouvé dans la base).'
        niveau_msg = f" ({niveau_canon})" if niveau_canon else ""
        message += f' {nb_etudiants_inscrits} étudiants de {filiere_canon}{niveau_msg} ajoutés automatiquement.'

        flash(message, 'success')
        return redirect(url_for('admin_dashboard'))

    except Exception as e:
        flash(f'Erreur lors de la création du cours: {str(e)}', 'error')
        return redirect(request.url)


        import traceback
        error_msg = f"Erreur sauvegarde présences: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return jsonify({'success': False, 'message': 'Erreur lors de la sauvegarde'}), 500

# 🎯 ROUTES POUR GESTION DES CLASSES PAR PROFESSEUR
@app.route('/professeur/classes')
@login_required
def prof_classes():
    """Page de gestion des classes pour les professeurs - Affiche uniquement les classes concernées par les modules enseignés"""
    if session.get('role') != 'professeur':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('professeur_emploi_temps'))

    cursor = conn.cursor(dictionary=True)
    
    # Vérifier si la colonne niveau existe dans courses
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    niveau_exists = cursor.fetchone() is not None
    
    # Récupérer les combinaisons (filière, niveau) distinctes des cours où ce professeur enseigne
    if niveau_exists:
        cursor.execute("""
            SELECT DISTINCT c.filiere, c.niveau
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' AND et.visible = 1
            AND c.filiere IS NOT NULL AND c.filiere != ''
            AND c.niveau IS NOT NULL AND c.niveau != ''
            ORDER BY c.filiere ASC, c.niveau ASC
        """, (user_id,))
    else:
        # Si la colonne niveau n'existe pas, récupérer seulement les filières
        cursor.execute("""
            SELECT DISTINCT c.filiere, NULL as niveau
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' AND et.visible = 1
            AND c.filiere IS NOT NULL AND c.filiere != ''
            ORDER BY c.filiere ASC
        """, (user_id,))
    
    cours_classes = cursor.fetchall()
    
    # Si le professeur n'a aucun cours, afficher un message
    if not cours_classes:
        conn.close()
        return render_template("prof_classes.html", 
                             classes_par_filiere={},
                             nom=session.get('nom', ''),
                             prenom=session.get('prenom', ''),
                             message="Vous n'enseignez actuellement aucun cours. Les classes apparaîtront ici une fois qu'un administrateur vous assignera des cours.")
    
    # Créer un set des combinaisons (filière, niveau) uniques
    classes_enseignees = set()
    for row in cours_classes:
        filiere = row['filiere']
        niveau = row.get('niveau')
        if niveau:
            # Normaliser le niveau (prendre L1, L2, etc. même si c'est "Licence 1")
            niveau_normalise = niveau.split()[0] if niveau else ""
            classes_enseignees.add((filiere, niveau_normalise, niveau))
        else:
            # Si pas de niveau dans le cours, on ne peut pas déterminer la classe
            continue
    
    # Récupérer le nombre d'étudiants pour chaque classe enseignée
    classes_avec_counts = []
    for filiere, niveau_abbrev, niveau_complet in classes_enseignees:
        # Chercher les étudiants avec cette filière et ce niveau
        # Le niveau dans users peut être "Licence 1", "L1", etc.
        # On cherche avec plusieurs patterns pour couvrir tous les cas
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM users
            WHERE role = 'etudiant'
            AND filiere = %s
            AND niveau IS NOT NULL AND niveau != ''
            AND (
                niveau = %s
                OR niveau = %s
                OR niveau LIKE %s
                OR niveau LIKE %s
            )
            AND school_id = %s
        """, (filiere, niveau_complet, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%', tenant.current_school_id()))
        
        result = cursor.fetchone()
        count = result['count'] if result else 0
        
        # Trouver le niveau complet réel dans la base de données (le plus courant)
        cursor.execute("""
            SELECT niveau, COUNT(*) as cnt
            FROM users
            WHERE role = 'etudiant'
            AND filiere = %s
            AND niveau IS NOT NULL AND niveau != ''
            AND (
                niveau = %s
                OR niveau = %s
                OR niveau LIKE %s
                OR niveau LIKE %s
            )
            AND school_id = %s
            GROUP BY niveau
            ORDER BY cnt DESC
            LIMIT 1
        """, (filiere, niveau_complet, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%', tenant.current_school_id()))
        
        niveau_result = cursor.fetchone()
        niveau_final = niveau_result['niveau'] if niveau_result else niveau_complet
        
        classes_avec_counts.append({
            'filiere': filiere,
            'niveau': niveau_final,
            'count': count
        })
    
    # Fonction pour générer le nom de classe
    def generer_nom_classe(niveau, filiere):
        niveau_abbrev = niveau.split()[0] if niveau else ""
        filiere_abbrev_map = {
            'Intelligence Artificielle': 'IA', 'IA': 'IA',
            'Comptabilité Contrôle Audit': 'CCA', 'CCA': 'CCA',
            'Finance': 'FINANCE', 'Finance et Gestion': 'FINANCE',
            'Médecine': 'MEDS', 'MEDS': 'MEDS',
            'Marketing': 'MARKETING', 'Marketing Digital': 'MARKETING'
        }
        filiere_abbrev = filiere_abbrev_map.get(filiere, filiere.upper()[:8] if filiere else "")
        return f"{niveau_abbrev}-{filiere_abbrev}"

    # Organiser les classes par filière (uniquement les classes concernées par les cours)
    classes_par_filiere = {}
    
    for classe_info in classes_avec_counts:
        filiere = classe_info['filiere']
        niveau = classe_info['niveau']
        count = classe_info['count']
        
        if filiere not in classes_par_filiere:
            classes_par_filiere[filiere] = []
        
        nom_classe = generer_nom_classe(niveau, filiere)
        
        classes_par_filiere[filiere].append({
            'nom_classe': nom_classe,
            'niveau': niveau,
            'count': count
        })
    
    # Trier les classes par niveau dans chaque filière
    for filiere in classes_par_filiere:
        # Ordre de tri : L1, L2, L3, M1, M2
        ordre_niveaux = {'L1': 1, 'L2': 2, 'L3': 3, 'M1': 4, 'M2': 5}
        classes_par_filiere[filiere].sort(key=lambda x: ordre_niveaux.get(x['niveau'].split()[0].upper(), 99))

    conn.close()
    return render_template("prof_classes.html", 
                         classes_par_filiere=classes_par_filiere,
                         nom=session.get('nom', ''),
                         prenom=session.get('prenom', ''))

@app.route('/professeur/classes/<filiere>/<niveau>')
@login_required
def prof_class_details(filiere, niveau):
    """Redirection vers la page des classes - contenu supprimé"""
    if session.get('role') != 'professeur':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))

    # Redirection vers la page des classes
    return redirect(url_for('prof_classes'))

@app.route('/professeur/classes/<filiere>/<niveau>/save-presences', methods=['POST'])
@login_required
def prof_save_presences(filiere, niveau):
    """Sauvegarder les présences pour une classe"""
    from urllib.parse import unquote
    from datetime import datetime
    
    if session.get('role') != 'professeur':
        return jsonify({'success': False, 'message': 'Accès refusé'}), 403

    filiere = unquote(filiere)
    niveau = unquote(niveau)
    
    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

    cursor = conn.cursor(dictionary=True)
    
    # Récupérer les données du formulaire
    data = request.get_json()
    course_id = data.get('course_id')
    date_cours = data.get('date_cours', datetime.now().strftime('%Y-%m-%d'))
    presences = data.get('presences', {})
    
    if not course_id:
        conn.close()
        return jsonify({'success': False, 'message': 'Cours requis'}), 400
    
    # Vérifier que le professeur a accès à ce cours
    # Vérifier si la colonne niveau existe dans courses
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    niveau_exists = cursor.fetchone() is not None
    
    f_aliases = filiere_aliases(cursor, filiere) or ([filiere] if filiere else [])
    n_aliases = niveau_aliases(niveau) if niveau_exists else []
    f_clause = (
        f"c.filiere IN ({','.join(['%s'] * len(f_aliases))})"
        if f_aliases else "1=1"
    )
    if n_aliases:
        n_clause = (
            f"AND (c.niveau IN ({','.join(['%s'] * len(n_aliases))}) "
            f"OR c.niveau IS NULL OR c.niveau = '')"
        )
        cursor.execute(f"""
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
            AND {f_clause}
            {n_clause}
        """, (course_id, user_id, *f_aliases, *n_aliases))
    else:
        cursor.execute(f"""
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
            AND {f_clause}
        """, (course_id, user_id, *f_aliases))
    
    course = cursor.fetchone()
    if not course:
        conn.close()
        return jsonify({'success': False, 'message': 'Cours non trouvé ou accès refusé pour cette classe'}), 404
    
    # Sauvegarder chaque présence
    year_id = get_current_year_id()
    for etudiant_id, presence_info in presences.items():
        statut = presence_info.get('statut', 'absent')
        commentaire = presence_info.get('commentaire', '')

        # Vérifier si une présence existe déjà
        cursor.execute("""
            SELECT id FROM presences
            WHERE course_id = %s AND etudiant_id = %s AND date_cours = %s
        """, (course_id, etudiant_id, date_cours))

        existing = cursor.fetchone()

        if existing:
            # Mettre à jour la présence existante
            cursor.execute("""
                UPDATE presences
                SET statut = %s, commentaire = %s, updated_at = NOW()
                WHERE id = %s AND school_id = %s
            """, (statut, commentaire, existing['id'], tenant.current_school_id()))
        else:
            # Créer une nouvelle présence
            cursor.execute("""
                INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, created_at, updated_at, annee_academique_id, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)
            """, (etudiant_id, course_id, user_id, date_cours, statut, commentaire, year_id, tenant.current_school_id()))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Présences sauvegardées avec succès'})

# 🎯 ROUTE POUR LES ABSENCES ADMIN
# 🎯 NOTIFICATIONS & ALERTES TEMPS RÉEL
@app.route('/api/notifications')
@login_required
def api_notifications():
    """Notifications in-app pour l'utilisateur connecté"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'notifications': []})
        cursor = conn.cursor(dictionary=True)
        ensure_notifications_table(cursor)
        cursor.execute("""
            SELECT id, type, title, message, link, is_read, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 20
        """, (session['user_id'],))
        notifs = cursor.fetchall()
        for n in notifs:
            if n.get('created_at'):
                n['created_at'] = n['created_at'].strftime('%d/%m/%Y %H:%M')
        cursor.execute(
            "SELECT COUNT(*) as c FROM notifications WHERE user_id = %s AND is_read = 0",
            (session['user_id'],)
        )
        unread = (cursor.fetchone() or {}).get('c', 0)
        conn.close()
        return jsonify({'success': True, 'notifications': notifs, 'unread': unread})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def api_mark_notification_read(notif_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s",
            (notif_id, session['user_id'])
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/student/absences/recent')
@login_required
def api_student_absences_recent():
    if session.get('role') != 'etudiant':
        return jsonify({'success': False}), 403
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        school_id = tenant.current_school_id()
        if year_id:
            cursor.execute("""
                SELECT p.date_cours, p.statut, p.created_at, c.nom_cours, c.salle,
                       prof.prenom as prof_prenom, prof.nom as prof_nom
                FROM presences p
                JOIN courses c ON p.course_id = c.id
                LEFT JOIN users prof ON p.professeur_id = prof.id
                WHERE p.etudiant_id = %s AND p.statut IN ('absent', 'retard')
                  AND p.annee_academique_id = %s AND p.school_id = %s
                ORDER BY p.created_at DESC LIMIT 10
            """, (session['user_id'], year_id, school_id))
        else:
            cursor.execute("""
                SELECT p.date_cours, p.statut, p.created_at, c.nom_cours, c.salle,
                       prof.prenom as prof_prenom, prof.nom as prof_nom
                FROM presences p
                JOIN courses c ON p.course_id = c.id
                LEFT JOIN users prof ON p.professeur_id = prof.id
                WHERE p.etudiant_id = %s AND p.statut IN ('absent', 'retard')
                  AND p.school_id = %s
                ORDER BY p.created_at DESC LIMIT 10
            """, (session['user_id'], school_id))
        rows = cursor.fetchall()
        for r in rows:
            if r.get('date_cours'):
                r['date_cours'] = str(r['date_cours'])[:10]
            if r.get('created_at'):
                r['created_at'] = r['created_at'].strftime('%d/%m/%Y %H:%M')
        cursor.execute("""
            SELECT COUNT(*) as c FROM presences
            WHERE etudiant_id = %s AND statut IN ('absent', 'retard') AND school_id = %s
        """ + (" AND annee_academique_id = %s" if year_id else ""),
            (session['user_id'], school_id, year_id) if year_id else (session['user_id'], school_id))
        total = (cursor.fetchone() or {}).get('c', 0)
        conn.close()
        return jsonify({'success': True, 'absences': rows, 'total': total})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/absences/recent')
@login_required
@admin_required
def api_admin_absences_recent():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        year_id = get_current_year_id()
        yf = " AND p.annee_academique_id = %s" if year_id else ""
        pr = (year_id,) if year_id else ()
        cursor.execute(f"""
            SELECT p.id, p.date_cours, p.statut, p.created_at,
                   u.prenom, u.nom, u.filiere, c.nom_cours
            FROM presences p
            JOIN users u ON p.etudiant_id = u.id
            JOIN courses c ON p.course_id = c.id
            WHERE p.statut IN ('absent', 'retard'){yf}
            ORDER BY p.created_at DESC LIMIT 15
        """, pr)
        rows = cursor.fetchall()
        for r in rows:
            if r.get('created_at'):
                r['created_at'] = r['created_at'].strftime('%d/%m/%Y %H:%M')
        conn.close()
        return jsonify({'success': True, 'absences': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/absences')
@login_required
@admin_required
def admin_absences():
    """Page de gestion des absences pour l'admin"""
    try:
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_home'))

        cursor = conn.cursor(dictionary=True)

        # Filtrer par année académique
        year_id = get_current_year_id()

        # Récupérer toutes les absences avec les détails des étudiants et cours
        if year_id:
            cursor.execute("""
                SELECT p.*, u.nom, u.prenom, u.filiere, u.niveau, c.nom_cours, c.salle,
                       prof.nom as prof_nom, prof.prenom as prof_prenom
                FROM presences p
                JOIN users u ON p.etudiant_id = u.id
                JOIN courses c ON p.course_id = c.id
                JOIN users prof ON p.professeur_id = prof.id
                WHERE p.statut = 'absent' AND p.annee_academique_id = %s
                ORDER BY p.date_cours DESC, p.created_at DESC
            """, (year_id,))
        else:
            cursor.execute("""
                SELECT p.*, u.nom, u.prenom, u.filiere, u.niveau, c.nom_cours, c.salle,
                       prof.nom as prof_nom, prof.prenom as prof_prenom
                FROM presences p
                JOIN users u ON p.etudiant_id = u.id
                JOIN courses c ON p.course_id = c.id
                JOIN users prof ON p.professeur_id = prof.id
                WHERE p.statut = 'absent'
                ORDER BY p.date_cours DESC, p.created_at DESC
            """)

        absences = cursor.fetchall()

        # Calculer les statistiques
        if year_id:
            cursor.execute("""
                SELECT
                    COUNT(*) as total_absences,
                    COUNT(DISTINCT etudiant_id) as etudiants_absents,
                    COUNT(DISTINCT course_id) as cours_concernes
                FROM presences
                WHERE statut = 'absent' AND annee_academique_id = %s
            """, (year_id,))
        else:
            cursor.execute("""
                SELECT
                    COUNT(*) as total_absences,
                    COUNT(DISTINCT etudiant_id) as etudiants_absents,
                    COUNT(DISTINCT course_id) as cours_concernes
                FROM presences
                WHERE statut = 'absent'
            """)

        stats = cursor.fetchone()

        conn.close()

        return render_template('admin_absences.html', absences=absences, stats=stats)

    except Exception as e:
        import traceback
        error_msg = f"Erreur admin absences: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement des absences.", "danger")
        return redirect(url_for('admin_home'))

# 🎯 ROUTE POUR LES ABSENCES ÉTUDIANT
@app.route('/student/absences')
@login_required
def student_absences():
    """Page des absences pour l'étudiant"""
    try:
        if session.get('role') != 'etudiant':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('student_dashboard'))

        cursor = conn.cursor(dictionary=True)

        # Filtrer par année académique
        year_id = get_current_year_id()

        # Récupérer uniquement les absences de cet étudiant (scopé école)
        school_id = tenant.current_school_id()
        if year_id:
            cursor.execute("""
                SELECT p.*, c.nom_cours, c.salle, c.heure_debut, c.heure_fin,
                       prof.nom as prof_nom, prof.prenom as prof_prenom
                FROM presences p
                JOIN courses c ON p.course_id = c.id
                JOIN users prof ON p.professeur_id = prof.id
                WHERE p.etudiant_id = %s AND p.statut = 'absent'
                  AND p.annee_academique_id = %s AND p.school_id = %s
                ORDER BY p.date_cours DESC, p.created_at DESC
            """, (user_id, year_id, school_id))
        else:
            cursor.execute("""
                SELECT p.*, c.nom_cours, c.salle, c.heure_debut, c.heure_fin,
                       prof.nom as prof_nom, prof.prenom as prof_prenom
                FROM presences p
                JOIN courses c ON p.course_id = c.id
                JOIN users prof ON p.professeur_id = prof.id
                WHERE p.etudiant_id = %s AND p.statut = 'absent' AND p.school_id = %s
                ORDER BY p.date_cours DESC, p.created_at DESC
            """, (user_id, school_id))

        absences = cursor.fetchall()

        # Calculer les statistiques personnelles (absences uniquement)
        if year_id:
            cursor.execute("""
                SELECT
                    COUNT(*) as total_absences,
                    COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN 1 END) as absences_30j,
                    COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 1 END) as absences_7j
                FROM presences
                WHERE etudiant_id = %s AND statut = 'absent'
                  AND annee_academique_id = %s AND school_id = %s
            """, (user_id, year_id, school_id))
        else:
            cursor.execute("""
                SELECT
                    COUNT(*) as total_absences,
                    COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN 1 END) as absences_30j,
                    COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 1 END) as absences_7j
                FROM presences
                WHERE etudiant_id = %s AND statut = 'absent' AND school_id = %s
            """, (user_id, school_id))

        stats = cursor.fetchone()

        conn.close()

        return render_template('student_absences.html', absences=absences, stats=stats)

    except Exception as e:
        import traceback
        error_msg = f"Erreur student absences: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement des absences.", "danger")
        return redirect(url_for('student_dashboard'))

@app.route('/professeur/upload-document/<int:course_id>')
def professeur_upload_document(course_id):
    """Page d'upload de document pour un professeur"""
    try:
        if 'user_id' not in session or session.get('role') != 'professeur':
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('professeur_emploi_temps'))

        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que le cours appartient à ce professeur
        cursor.execute("""
            SELECT c.*, u.prenom, u.nom
            FROM courses c
            JOIN users u ON c.professeur_id = u.id
            WHERE c.id = %s AND c.professeur_id = %s
        """, (course_id, session['user_id']))
        
        course = cursor.fetchone()
        
        if not course:
            flash("Cours non trouvé ou accès refusé.", "danger")
            conn.close()
            return redirect(url_for('professeur_emploi_temps'))

        conn.close()
        
        return render_template('professeur_upload_document.html', 
                               course=course, 
                               prenom=course['prenom'])

    except Exception as e:
        import traceback
        error_msg = f"Erreur upload document: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement de la page d'upload.", "error")
        return redirect(url_for('professeur_emploi_temps'))

@app.route('/professeur/upload-document/<int:course_id>', methods=['POST'])
def upload_document_post(course_id):
    """Upload d'un document pour un cours"""
    try:
        if 'user_id' not in session or session.get('role') != 'professeur':
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        # Vérifier que le fichier est présent
        if 'document' not in request.files:
            flash("Aucun fichier sélectionné.", "error")
            return redirect(url_for('professeur_upload_document', course_id=course_id))

        file = request.files['document']
        if file.filename == '':
            flash("Aucun fichier sélectionné.", "error")
            return redirect(url_for('professeur_upload_document', course_id=course_id))

        # Vérifier l'extension du fichier
        allowed_extensions = {
            # Documents
            'pdf', 'doc', 'docx', 'ppt', 'pptx', 'txt', 'rtf', 'odt', 'ods', 'odp',
            # Images
            'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'tiff',
            # Tableurs
            'xls', 'xlsx', 'csv',
            # Archives
            'zip', 'rar', '7z', 'tar', 'gz',
            # Code
            'py', 'js', 'html', 'css', 'json', 'xml', 'sql',
            # Audio/Video
            'mp3', 'mp4', 'avi', 'mov', 'wav', 'flv', 'mkv',
            # Autres
            'epub', 'mobi', 'azw', 'djvu'
        }
        if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            flash("Type de fichier non autorisé.", "error")
            return redirect(url_for('professeur_upload_document', course_id=course_id))

        # Vérifier la taille du fichier (10MB max)
        file_content = file.read()
        if len(file_content) > 10 * 1024 * 1024:
            flash("Fichier trop volumineux (max 10MB).", "error")
            return redirect(url_for('professeur_upload_document', course_id=course_id))
        
        file.seek(0)  # Reset file pointer

        # Récupérer les données du formulaire
        titre = request.form.get('titre', '').strip()
        description = request.form.get('description', '').strip()
        type_doc = request.form.get('type_doc', 'cours')

        if not titre:
            flash("Le titre du document est requis.", "error")
            return redirect(url_for('professeur_upload_document', course_id=course_id))

        # Générer un nom de fichier unique
        import os
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{session['user_id']}_{course_id}_{file.filename}"
        
        # Créer le dossier uploads s'il n'existe pas
        upload_folder = os.path.join(os.getcwd(), 'uploads')
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
        
        # Sauvegarder le fichier
        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)

        # Sauvegarder en base de données
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('professeur_upload_document', course_id=course_id))

        cursor = conn.cursor(dictionary=True)
        
        # Récupérer le nom_cours et la filiere du cours
        cursor.execute("SELECT nom_cours, filiere FROM courses WHERE id = %s", (course_id,))
        course_info = cursor.fetchone()
        
        if not course_info:
            flash("Cours non trouvé.", "danger")
            conn.close()
            return redirect(url_for('professeur_upload_document', course_id=course_id))
        
        nom_cours = course_info['nom_cours']
        filiere_cours = course_info['filiere']
        
        # Vérifier si les colonnes nom_cours et filiere existent dans documents
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
        nom_cours_exists = cursor.fetchone() is not None
        
        if not nom_cours_exists:
            # Ajouter les colonnes si elles n'existent pas
            try:
                cursor.execute("ALTER TABLE documents ADD COLUMN nom_cours VARCHAR(255) AFTER course_id")
                cursor.execute("ALTER TABLE documents ADD COLUMN filiere VARCHAR(255) AFTER nom_cours")
                conn.commit()
            except Exception as e:
                print(f"Erreur ajout colonnes nom_cours/filiere: {e}")
        
        # Insérer le document avec nom_cours et filiere
        # Après avoir ajouté les colonnes, elles existent maintenant
        try:
            cursor.execute("""
                INSERT INTO documents (course_id, nom_cours, filiere, professeur_id, titre, description, type_doc, nom_fichier, chemin_fichier, taille_fichier, date_upload, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (course_id, nom_cours, filiere_cours, session['user_id'], titre, description, type_doc, file.filename, filename, len(file_content), tenant.current_school_id()))
        except Exception as e:
            # Si erreur (colonnes n'existent pas), utiliser l'ancienne structure
            print(f"Erreur insertion avec nom_cours/filiere, utilisation ancienne structure: {e}")
            cursor.execute("""
                INSERT INTO documents (course_id, professeur_id, titre, description, type_doc, nom_fichier, chemin_fichier, taille_fichier, date_upload, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (course_id, session['user_id'], titre, description, type_doc, file.filename, filename, len(file_content), tenant.current_school_id()))
        
        conn.commit()
        conn.close()

        flash(f"Document '{titre}' uploadé avec succès !", "success")
        return redirect(url_for('professeur_upload_document', course_id=course_id))

    except Exception as e:
        import traceback
        error_msg = f"Erreur upload document: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors de l'upload du document.", "error")
        return redirect(url_for('professeur_upload_document', course_id=course_id))

@app.route('/student/course-documents/<int:course_id>')
@login_required
def student_course_documents(course_id):
    """Page des documents d'un cours pour un étudiant"""
    try:
        if 'user_id' not in session or session.get('role') != 'etudiant':
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('student_dashboard'))

        cursor = conn.cursor(dictionary=True)

        # Récupérer les informations du cours et vérifier l'inscription (course_id + school_id)
        _et_join = student_enrollment_join_sql('c', 'et')
        _tenant_w, _tenant_p = student_course_tenant_where('c', 'et')
        cursor.execute(f"""
            SELECT c.*, u.prenom as prof_prenom, u.nom as prof_nom
            FROM courses c
            JOIN users u ON c.professeur_id = u.id
            {_et_join}
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'etudiant'
            {_tenant_w}
        """, (course_id, session['user_id'], *_tenant_p))

        course = cursor.fetchone()

        if not course:
            flash("Cours non trouvé.", "danger")
            conn.close()
            return redirect(url_for('student_dashboard'))

        # Récupérer les documents du cours (par course_id)
        cursor.execute("""
            SELECT d.*, u.prenom as prof_prenom, u.nom as prof_nom
            FROM documents d
            JOIN users u ON d.professeur_id = u.id
            WHERE d.course_id = %s AND d.school_id = %s
            AND d.date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
            ORDER BY d.date_upload DESC
        """, (course_id, tenant.current_school_id()))
        
        documents = cursor.fetchall()

        conn.close()
        
        return render_template('student_course_documents.html', 
                               course=course, 
                               documents=documents)

    except Exception as e:
        import traceback
        error_msg = f"Erreur course documents: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement des documents.", "error")
        return redirect(url_for('student_dashboard'))

@app.route('/download-document/<int:document_id>')
def download_document_new(document_id):
    """Télécharger un document"""
    try:
        if 'user_id' not in session:
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('student_dashboard'))

        cursor = conn.cursor(dictionary=True)
        
        # Récupérer les informations du document
        cursor.execute("""
            SELECT d.*, c.nom_cours, c.filiere, c.niveau
            FROM documents d
            JOIN courses c ON d.course_id = c.id
            WHERE d.id = %s AND d.school_id = %s
        """, (document_id, tenant.current_school_id()))
        
        document = cursor.fetchone()
        
        if not document:
            flash("Document non trouvé.", "danger")
            conn.close()
            return redirect(url_for('student_dashboard'))

        # Vérifier les permissions (étudiant ou professeur du cours)
        if session.get('role') == 'etudiant':
            # Vérifier que l'étudiant est dans la même filière et niveau
            cursor.execute("""
                SELECT u.filiere, u.niveau
                FROM users u
                WHERE u.id = %s
            """, (session['user_id'],))
            
            student = cursor.fetchone()
            
            if not student or student['filiere'] != document['filiere'] or student['niveau'] != document['niveau']:
                flash("Accès non autorisé à ce document.", "danger")
                conn.close()
                return redirect(url_for('student_dashboard'))
        
        conn.close()

        # Télécharger le fichier
        import os
        from flask import send_file
        
        file_path = os.path.join(os.getcwd(), 'uploads', document['chemin_fichier'])
        
        if not os.path.exists(file_path):
            flash("Fichier non trouvé sur le serveur.", "error")
            return redirect(url_for('student_dashboard'))
        
        return send_file(file_path, as_attachment=True, download_name=document['nom_fichier'])

    except Exception as e:
        import traceback
        error_msg = f"Erreur download document: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du téléchargement du document.", "error")
        return redirect(url_for('student_dashboard'))

# ============================================
# 🤖 CHATBOT PDF INTELLIGENT POUR ÉTUDIANTS
# ============================================

# ============================================================
# 🤖 CHATBOT INTELLIGENT - AdsClass AI
# Configuration IA + appels modèles déplacés vers routes/chatbot_ai.py
# Cluster chatbot étudiant déplacé vers routes/chatbot_student.py
# Cluster chatbot admin déplacé vers routes/chatbot_admin.py
# ============================================================

import json


# Cluster administration (roles/permissions) deplace vers routes/admin.py


# ============================================================
# 📅 GESTION DES ANNÉES ACADÉMIQUES
# ============================================================

def get_current_academic_year():
    """Récupérer l'année académique active ou sélectionnée"""
    # Priorité: session > année active en base
    if 'annee_academique_id' in session:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM academic_years WHERE id = %s AND school_id = %s", (session['annee_academique_id'], tenant.current_school_id()))
            year = cursor.fetchone()
            cursor.close()
            conn.close()
            if year:
                return year

    # Sinon, récupérer l'année active
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM academic_years WHERE est_active = TRUE AND school_id = %s LIMIT 1", (tenant.current_school_id(),))
        year = cursor.fetchone()
        cursor.close()
        conn.close()
        if year:
            session['annee_academique_id'] = year['id']
            return year
    return None

def get_current_year_id():
    """Récupérer l'ID de l'année académique courante (pour les filtres SQL)"""
    if 'annee_academique_id' in session:
        return session['annee_academique_id']
    year = get_current_academic_year()
    return year['id'] if year else None

def init_year_columns():
    """Ajouter la colonne annee_academique_id aux tables si elle n'existe pas"""
    try:
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()

        # Tables qui doivent avoir annee_academique_id
        tables = ['presences', 'notes', 'gradebook', 'courses', 'paiements', 'depenses']

        for table in tables:
            try:
                cursor.execute(f"SHOW COLUMNS FROM {table} LIKE 'annee_academique_id'")
                if not cursor.fetchone():
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN annee_academique_id INT")
                    print(f"Colonne annee_academique_id ajoutée à {table}")
            except Exception as e:
                print(f"Note: {table} - {e}")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur init_year_columns: {e}")

# Initialiser les colonnes au démarrage
init_year_columns()

def get_all_academic_years():
    """Récupérer toutes les années académiques"""
    conn = get_db_connection()
    if not conn:
        return []
    cursor = conn.cursor(dictionary=True)
    _sid = tenant.current_school_id()
    cursor.execute("""
        SELECT *,
               (SELECT COUNT(*) FROM courses WHERE annee_academique_id = academic_years.id AND school_id = %s) as nb_cours,
               (SELECT COUNT(*) FROM paiements WHERE annee_academique_id = academic_years.id AND school_id = %s) as nb_paiements
        FROM academic_years
        WHERE school_id = %s
        ORDER BY date_debut DESC
    """, (_sid, _sid, _sid))
    years = cursor.fetchall()
    cursor.close()
    conn.close()
    return years

# Injecter l'année académique dans tous les templates
@app.context_processor
def inject_academic_year():
    if 'user_id' in session:
        current_year = get_current_academic_year()
        all_years = get_all_academic_years()
        return {
            'current_academic_year': current_year,
            'all_academic_years': all_years
        }
    return {'current_academic_year': None, 'all_academic_years': []}


@app.route('/admin/academic-years')
@login_required
@admin_required
@require_permission('system.settings')
def admin_academic_years():
    """Page de gestion des années académiques"""
    years = get_all_academic_years()
    return render_template('admin_academic_years.html', years=years)


@app.route('/admin/academic-years/create', methods=['POST'])
@login_required
@admin_required
@require_permission('system.settings')
def create_academic_year():
    """Créer une nouvelle année académique"""
    nom = request.form.get('nom')
    date_debut = request.form.get('date_debut')
    date_fin = request.form.get('date_fin')
    description = request.form.get('description', '')

    if not nom or not date_debut or not date_fin:
        flash('Tous les champs obligatoires doivent être remplis.', 'danger')
        return redirect(url_for('admin_academic_years'))

    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données.', 'danger')
        return redirect(url_for('admin_academic_years'))

    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO academic_years (nom, date_debut, date_fin, description, school_id)
            VALUES (%s, %s, %s, %s, %s)
        ''', (nom, date_debut, date_fin, description, tenant.current_school_id()))
        conn.commit()
        flash(f'Année académique {nom} créée avec succès!', 'success')
    except Error as e:
        if 'Duplicate' in str(e):
            flash(f'L\'année académique {nom} existe déjà.', 'warning')
        else:
            flash(f'Erreur: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin_academic_years'))


@app.route('/admin/academic-years/<int:year_id>/activate', methods=['POST'])
@login_required
@admin_required
@require_permission('system.settings')
def activate_academic_year(year_id):
    """Activer une année académique"""
    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion.', 'danger')
        return redirect(url_for('admin_academic_years'))

    try:
        cursor = conn.cursor()
        # Désactiver toutes les années
        cursor.execute("UPDATE academic_years SET est_active = FALSE WHERE school_id = %s",
                       (tenant.current_school_id(),))
        # Activer l'année sélectionnée
        cursor.execute("UPDATE academic_years SET est_active = TRUE WHERE id = %s AND school_id = %s",
                       (year_id, tenant.current_school_id()))
        conn.commit()

        # Mettre à jour la session
        session['annee_academique_id'] = year_id

        flash('Année académique activée avec succès!', 'success')
    except Error as e:
        flash(f'Erreur: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin_academic_years'))


@app.route('/admin/academic-years/<int:year_id>/edit', methods=['POST'])
@login_required
@admin_required
@require_permission('system.settings')
def edit_academic_year(year_id):
    """Modifier une année académique"""
    nom = request.form.get('nom')
    date_debut = request.form.get('date_debut')
    date_fin = request.form.get('date_fin')
    description = request.form.get('description', '')

    if not nom or not date_debut or not date_fin:
        flash('Tous les champs obligatoires doivent être remplis.', 'danger')
        return redirect(url_for('admin_academic_years'))

    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données.', 'danger')
        return redirect(url_for('admin_academic_years'))

    try:
        cursor = conn.cursor(dictionary=True)
        # Vérifier que l'année n'est pas archivée
        cursor.execute("SELECT est_archivee FROM academic_years WHERE id = %s", (year_id,))
        year = cursor.fetchone()

        if year and year['est_archivee']:
            flash('Impossible de modifier une année archivée.', 'warning')
            return redirect(url_for('admin_academic_years'))

        cursor.execute('''
            UPDATE academic_years
            SET nom = %s, date_debut = %s, date_fin = %s, description = %s
            WHERE id = %s AND school_id = %s
        ''', (nom, date_debut, date_fin, description, year_id, tenant.current_school_id()))
        conn.commit()
        flash(f'Année académique {nom} modifiée avec succès!', 'success')
    except Error as e:
        if 'Duplicate' in str(e):
            flash(f'Une année académique avec ce nom existe déjà.', 'warning')
        else:
            flash(f'Erreur: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin_academic_years'))


@app.route('/admin/academic-years/<int:year_id>/archive', methods=['POST'])
@login_required
@admin_required
@require_permission('system.settings')
def archive_academic_year(year_id):
    """Archiver une année académique (lecture seule, non active)"""
    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion.', 'danger')
        return redirect(url_for('admin_academic_years'))

    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE academic_years SET est_archivee = TRUE, est_active = FALSE "
            "WHERE id = %s AND school_id = %s",
            (year_id, tenant.current_school_id()))
        conn.commit()
        flash('Année académique archivée avec succès!', 'success')
    except Error as e:
        flash(f'Erreur: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin_academic_years'))


@app.route('/api/switch-academic-year', methods=['POST'])
@login_required
def switch_academic_year():
    """Changer l'année académique active pour la session"""
    data = request.get_json()
    year_id = data.get('year_id')

    if not year_id:
        return jsonify({'success': False, 'message': 'ID année manquant'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Erreur connexion'}), 500

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM academic_years WHERE id = %s", (year_id,))
    year = cursor.fetchone()
    cursor.close()
    conn.close()

    if not year:
        return jsonify({'success': False, 'message': 'Année non trouvée'}), 404

    session['annee_academique_id'] = year_id
    return jsonify({
        'success': True,
        'message': f'Année {year["nom"]} sélectionnée',
        'year': year
    })


# ============================================================
# 🎓 GESTION DES FILIÈRES ET MODULES
# ============================================================

def _ensure_filieres_per_school_unique(cursor):
    """Rend l'unicité des filières (nom, code) propre à chaque école.

    Convertit les anciens index UNIQUE globaux mono-colonne (`nom`, `code`) en
    index UNIQUE composites (school_id, nom) / (school_id, code). Idempotent.
    Nécessite la colonne school_id (ajoutée par init_multi_tenant).
    """
    cursor.execute("SHOW COLUMNS FROM filieres LIKE 'school_id'")
    if not cursor.fetchone():
        return  # multi-tenant pas encore migré → rien à faire

    cursor.execute("SHOW INDEX FROM filieres")
    by_key = {}
    for r in cursor.fetchall():
        by_key.setdefault(r[2], {'non_unique': r[1], 'cols': []})
        by_key[r[2]]['cols'].append((r[3], r[4]))

    for legacy in ('nom', 'code'):
        info = by_key.get(legacy)
        cols = [c for _, c in sorted(info['cols'])] if info else []
        if info and info['non_unique'] == 0 and cols == [legacy]:
            try:
                cursor.execute(f"ALTER TABLE filieres DROP INDEX `{legacy}`")
            except Exception as ex:
                print(f"⚠️ filieres DROP INDEX {legacy}: {ex}")

    cursor.execute("SHOW INDEX FROM filieres")
    existing = {r[2] for r in cursor.fetchall()}
    composites = {
        'uq_filieres_school_nom': '(school_id, nom)',
        'uq_filieres_school_code': '(school_id, code)',
    }
    for name, cols in composites.items():
        if name not in existing:
            try:
                cursor.execute(f"ALTER TABLE filieres ADD UNIQUE KEY {name} {cols}")
            except Exception as ex:
                print(f"⚠️ filieres ADD UNIQUE {name}: {ex}")


def init_filieres_modules_tables():
    """Créer les tables filières et modules si elles n'existent pas"""
    try:
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()

        # Table filières (unicité nom/code gérée par école, cf. migration ci-dessous)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS filieres (
                id INT PRIMARY KEY AUTO_INCREMENT,
                nom VARCHAR(255) NOT NULL,
                code VARCHAR(50),
                description TEXT,
                niveau VARCHAR(100),
                duree_annees INT DEFAULT 1,
                est_active BOOLEAN DEFAULT TRUE,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        # Table modules
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS modules (
                id INT PRIMARY KEY AUTO_INCREMENT,
                filiere_id INT NOT NULL,
                nom VARCHAR(255) NOT NULL,
                code VARCHAR(50),
                description TEXT,
                credits INT DEFAULT 0,
                coefficient DOUBLE DEFAULT 1.0,
                semestre INT DEFAULT 1,
                niveau VARCHAR(100),
                est_obligatoire BOOLEAN DEFAULT TRUE,
                est_actif BOOLEAN DEFAULT TRUE,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (filiere_id) REFERENCES filieres(id) ON DELETE CASCADE,
                UNIQUE KEY unique_module (filiere_id, nom, semestre)
            )
        """)

        conn.commit()
        try:
            _ensure_filieres_per_school_unique(cursor)
            conn.commit()
        except Exception as ex:
            print(f"⚠️ Unicité filières par école: {ex}")
        try:
            ensure_student_account_columns(cursor)
            conn.commit()
        except Exception as ex:
            print(f"⚠️ Colonnes compte étudiant: {ex}")
        cursor.close()
        conn.close()
        print("✅ Tables filières et modules initialisées")
    except Exception as e:
        print(f"⚠️ Erreur init tables filières/modules: {e}")

# Initialiser les tables au démarrage
init_filieres_modules_tables()


def _standardize_filieres_niveaux_at_startup():
    """Backfill : 'IA' -> 'Intelligence Artificielle', 'M2' -> 'Master 2' partout."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        standardize_filieres_niveaux(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass


_standardize_filieres_niveaux_at_startup()


# ============================================================
# 📱 SYSTÈME DE QR CODE POUR PRÉSENCE
# ============================================================

def init_qr_presence_tables():
    """Créer la table pour les présences générales via QR code"""
    try:
        conn = get_db_connection()
        if not conn:
            return

        cursor = conn.cursor()

        # Table pour les présences générales (sans cours spécifique)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS presences_generales (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                date DATE NOT NULL,
                heure_scan DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY unique_presence (user_id, date),
                INDEX idx_date (date),
                INDEX idx_user (user_id)
            )
        """)

        # Ajouter une colonne heure_scan à la table presences existante si elle n'existe pas
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'presences'
            AND COLUMN_NAME = 'heure_scan'
        """)

        result = cursor.fetchone()
        if result and result[0] == 0:
            cursor.execute("""
                ALTER TABLE presences
                ADD COLUMN heure_scan DATETIME NULL AFTER statut
            """)

        conn.commit()
        conn.close()
        print("✅ Tables QR code présence créées avec succès")

    except Error as e:
        print(f"❌ Erreur création tables QR présence: {e}")


_init_professeur_classes_table()


# ============================================================
# 🎓 MODULE ACADÉMIQUE (filières / modules)
# ============================================================
from routes.academic import register_academic_routes
register_academic_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
})


# ============================================================
# 📜 MODULE ATTESTATIONS SCOLAIRES
# ============================================================
from routes.attestations import init_attestations_tables, register_attestations_routes
init_attestations_tables(get_db_connection)
register_attestations_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'get_current_year_id': get_current_year_id,
})


# ============================================================
# 📥 MODULE ADMISSIONS CRM
# ============================================================
from routes.admissions import init_admissions_tables, register_admissions_routes
init_admissions_tables(get_db_connection)
register_admissions_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
    'get_current_year_id': get_current_year_id,
    'generate_password_hash': generate_password_hash,
})


# ============================================================
# 🎓 MODULE ÉTUDIANTS (paiements / reçus)
# ============================================================
from routes.students import register_students_routes
register_students_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
    'get_current_year_id': get_current_year_id,
})


# ============================================================
# 👨‍🏫 MODULE ENSEIGNANTS (administration)
# ============================================================
from routes.teachers import register_teachers_routes
register_teachers_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
    '_init_professeur_classes_table': _init_professeur_classes_table,
})


# ============================================================
# 📝 MODULE NOTES / EXAMENS (administration)
# ============================================================
from routes.grades import register_grades_routes
register_grades_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
    'get_current_year_id': get_current_year_id,
})
from routes.chatbot_student import register_chatbot_student_routes
register_chatbot_student_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
})
from routes.chatbot_admin import register_chatbot_admin_routes
register_chatbot_admin_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
    'get_current_year_id': get_current_year_id,
})


# ============================================================
# 🔐 MODULE ADMINISTRATION (rôles / permissions)
# ============================================================
from routes.admin import register_admin_routes
register_admin_routes(app, {
    'get_db_connection': get_db_connection,
    'login_required': login_required,
    'admin_required': admin_required,
})


if __name__ == '__main__':
    # Initialiser les tables au démarrage
    init_qr_presence_tables()
    init_attestations_tables(get_db_connection)

    app.run(debug=DEBUG_MODE)