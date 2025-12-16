
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import mysql.connector
from mysql.connector import Error
from functools import wraps
import io
import csv
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "votre_clef_secrete"

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

# Connexion DB MySQL
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='adsclass_bd',
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
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

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('register'))
        
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO users (nom, prenom, email, password, role, filiere, niveau)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (nom, prenom, email, hashed_password, role, filiere, niveau))
            conn.commit()
        except mysql.connector.IntegrityError:
            flash("Cet email est déjà utilisé.")
            return redirect(url_for('register'))
        finally:
            conn.close()

        flash("Inscription réussie. Connectez-vous.")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        password = request.form['password']

        if not email.endswith('@adsclass.ne'):
            flash("L'email doit se terminer par @adsclass.ne", "danger")
            return render_template('login.html', email=email)

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return render_template('login.html', email=email)
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            # Session sécurisée
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['nom'] = user['nom']
            session['prenom'] = user['prenom']
            session['user_email'] = user['email']
            session['filiere'] = user['filiere'] if user['filiere'] else ''
            session['niveau'] = user['niveau'] if user['niveau'] else ''

            # Redirection selon le rôle
            if user['role'] == 'admin':
                return redirect(url_for('admin_home'))
            elif user['role'] == 'professeur':
                return redirect(url_for('professeur_emploi_temps'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash("Identifiants incorrects.", "danger")
            return render_template('login.html', email=email)

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

        # Insérer ou mettre à jour l'absence (MySQL utilise ON DUPLICATE KEY UPDATE)
        cursor.execute('''
        INSERT INTO presences
        (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        statut = VALUES(statut),
        commentaire = VALUES(commentaire),
        updated_at = VALUES(updated_at)
        ''', (etudiant_id, 1, session['user_id'], date_cours, statut, '',
              datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

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
    return render_template('admin_home.html')

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM courses ORDER BY start")
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

        # Insérer le cours avec tous les champs, y compris niveau
        cursor.execute(
            """INSERT INTO courses (nom_cours, professeur_id, professeur_nom, start, end, filiere, niveau, salle, description, jour_semaine, heure_debut, heure_fin, recurrent)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (nom_cours, professeur_id if professeur_id else None, professeur_nom, start, end, filiere, niveau, salle, description, jour_semaine, heure_debut, heure_fin, recurrent)
        )

        course_id = cursor.lastrowid

        # 🚀 AUTOMATISATION : Ajouter automatiquement le cours dans l'emploi du temps

        # 1. Ajouter pour tous les étudiants de la filière
        cursor.execute(
            "SELECT id FROM users WHERE role = 'etudiant' AND filiere = %s",
            (filiere,)
        )
        etudiants = cursor.fetchall()

        for etudiant in etudiants:
            try:
                cursor.execute(
                    "INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications) VALUES (%s, %s, %s, %s, %s)",
                    (etudiant['id'], course_id, 'etudiant', 1, 1)
                )
            except:
                pass  # Ignore les doublons

        # 2. Ajouter pour le professeur si spécifié
        if professeur_id:
            try:
                cursor.execute(
                    "INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications) VALUES (%s, %s, %s, %s, %s)",
                    (professeur_id, course_id, 'professeur', 1, 1)
                )
            except:
                pass  # Ignore les doublons

        conn.commit()
        conn.close()

        # Message de succès avec détails
        nb_etudiants = len(etudiants)
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
    cursor.execute("SELECT id, nom, prenom, specialite FROM users WHERE role = 'professeur' ORDER BY nom")
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
            "UPDATE courses SET nom_cours=%s, professeur=%s, start=%s, end=%s, filiere=%s WHERE id=%s",
            (nom_cours, professeur, start, end, filiere, course_id)
        )
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
    
    cursor = conn.cursor()
    cursor.execute("DELETE FROM courses WHERE id = %s", (course_id,))
    conn.commit()
    conn.close()
    flash("Cours supprimé.", "info")
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
    
    if niveau_exists and user_niveau:
        courses_query = """
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'etudiant' 
            AND c.filiere = %s 
            AND (c.niveau = %s OR c.niveau IS NULL OR c.niveau = '')
            ORDER BY c.start
        """
        cursor.execute(courses_query, (user_id, user_filiere, user_niveau))
    else:
        courses_query = """
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'etudiant' 
            AND c.filiere = %s
            ORDER BY c.start
        """
        cursor.execute(courses_query, (user_id, user_filiere))
    
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
        # Récupérer les documents par nom_cours et filiere (disponibles pendant 5 ans)
        cursor.execute('''
        SELECT DISTINCT d.id, d.titre, d.description, d.nom_fichier, d.date_upload,
               d.nom_cours, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN users u ON d.professeur_id = u.id
        JOIN courses c ON d.nom_cours = c.nom_cours AND d.filiere = c.filiere
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant' AND d.visible = 1
        AND d.date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
        ORDER BY d.date_upload DESC
        LIMIT 5
        ''', (session['user_id'],))
    else:
        # Compatibilité avec l'ancienne structure
        cursor.execute('''
        SELECT d.id, d.titre, d.description, d.nom_fichier, d.date_upload,
               c.nom_cours, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN courses c ON d.course_id = c.id
        JOIN users u ON d.professeur_id = u.id
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant' AND d.visible = 1
        ORDER BY d.date_upload DESC
        LIMIT 5
        ''', (session['user_id'],))
    documents_recents = cursor.fetchall()

    # Récupérer les absences récentes de l'étudiant
    cursor.execute('''
    SELECT p.date_cours, p.statut, p.commentaire, c.nom_cours,
           u.nom as prof_nom, u.prenom as prof_prenom
    FROM presences p
    JOIN courses c ON p.course_id = c.id
    JOIN users u ON p.professeur_id = u.id
    WHERE p.etudiant_id = %s AND p.statut != 'present'
    ORDER BY p.date_cours DESC
    LIMIT 5
    ''', (session['user_id'],))
    absences_recentes = cursor.fetchall()

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
    # Définir le filtre niveau
    if niveau_exists and user_niveau:
        niveau_filter = "AND (c.niveau = %s OR c.niveau IS NULL OR c.niveau = '')"
        params_cours = (user_id, user_filiere, user_niveau,
                        start_of_week_str, end_of_week_str,
                        start_of_week_str, end_of_week_str,
                        jours_semaine[0], jours_semaine[1], jours_semaine[2],
                        jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])
    else:
        niveau_filter = ""
        params_cours = (user_id, user_filiere,
                        start_of_week_str, end_of_week_str,
                        start_of_week_str, end_of_week_str,
                        jours_semaine[0], jours_semaine[1], jours_semaine[2],
                        jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])
    
    query_cours_semaine = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND c.filiere = %s 
        {niveau_filter}
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

    if niveau_exists and user_niveau:
        params_cours_last = (user_id, user_filiere, user_niveau,
                             start_last_week_str, end_last_week_str,
                             start_last_week_str, end_last_week_str,
                             jours_semaine[0], jours_semaine[1], jours_semaine[2],
                             jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])
    else:
        params_cours_last = (user_id, user_filiere,
                             start_last_week_str, end_last_week_str,
                             start_last_week_str, end_last_week_str,
                             jours_semaine[0], jours_semaine[1], jours_semaine[2],
                             jours_semaine[3], jours_semaine[4], jours_semaine[5], jours_semaine[6])
    
    query_cours_semaine_last = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND c.filiere = %s 
        {niveau_filter}
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
    stats_cursor.execute('''
        SELECT COUNT(*) as count FROM presences p
        WHERE p.etudiant_id = %s 
        AND p.statut IN ('absent', 'retard')
    ''', (user_id,))
    result = stats_cursor.fetchall()
    absences_total = result[0] if result else {'count': 0}

    # 3. Prochains examens (cours avec "examen" dans le nom ou description, ou date future)
    today_str = today.strftime('%Y-%m-%d')
    if niveau_exists and user_niveau:
        params_examens = (user_id, user_filiere, user_niveau, today_str, today_str)
    else:
        params_examens = (user_id, user_filiere, today_str, today_str)
    
    query_examens = f'''
        SELECT COUNT(DISTINCT c.id) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND c.filiere = %s 
        {niveau_filter}
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
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND c.filiere = %s 
        {niveau_filter}
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

    # Fermer le curseur des statistiques avant de fermer la connexion
    stats_cursor.close()
    conn.close()

    return render_template('student_dashboard.html',
                           events=events,
                           nom=session.get('nom', ''),
                           prenom=session.get('prenom', ''),
                           documents_recents=documents_recents,
                           absences_recentes=absences_recentes,
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

        for course in courses:
            try:
                # course est déjà un dict avec cursor(dictionary=True)
                course_dict = course

                if course_dict.get('jour_semaine') and course_dict['jour_semaine'] in emploi_temps:
                    # 🎯 RÉCUPÉRER LES ÉTUDIANTS UNIQUEMENT POUR CE COURS (SÉCURISÉ)
                    try:
                        cursor.execute(
                            "SELECT COUNT(*) as count FROM users WHERE role='etudiant' AND filiere = %s",
                            (course_dict['filiere'],)
                        )
                        nb_etudiants_result = cursor.fetchone()
                        nb_etudiants = nb_etudiants_result['count'] if nb_etudiants_result else 0
                    except Exception as e:
                        print(f"Erreur calcul étudiants: {e}")
                        nb_etudiants = 0

                    course_dict['nb_etudiants'] = nb_etudiants
                    emploi_temps[course_dict['jour_semaine']].append(course_dict)
            except Exception as e:
                print(f"Erreur traitement cours planning: {e}")
                continue

        conn.close()

        return render_template('professeur_emploi_temps.html',
                               emploi_temps=emploi_temps,
                               jours_semaine=jours_semaine,
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

        return render_template('professeur_emploi_temps.html',
                               emploi_temps=emploi_temps_vide,
                               jours_semaine=jours_semaine,
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''),
                               error_message="Erreur lors du chargement du planning. Veuillez réessayer.")

# 🎯 GESTION COMPLÈTE DU MODULE PAR LE PROFESSEUR
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
                    WHERE et.course_id = %s AND et.role = 'professeur'
                """, (course_id,))
                emploi_temps_data = cursor.fetchall()
                print(f"DEBUG: Emploi temps pour ce cours: {emploi_temps_data}")
            flash("Cours non trouvé ou accès refusé. Vérifiez que vous êtes bien assigné à ce cours.", "danger")
            conn.close()
            return redirect(url_for('professeur_emploi_temps'))

        # Récupérer les étudiants inscrits au module (par filière)
        # Vérifier si la colonne classe existe
        cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
        classe_exists = cursor.fetchone() is not None
        
        if classe_exists:
            cursor.execute("""
                SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau, u.classe
                FROM users u
                WHERE u.role = 'etudiant' AND u.filiere = %s
                ORDER BY u.nom, u.prenom
            """, (course['filiere'],))
        else:
            cursor.execute("""
                SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau, NULL as classe
                FROM users u
                WHERE u.role = 'etudiant' AND u.filiere = %s
                ORDER BY u.nom, u.prenom
            """, (course['filiere'],))
        etudiants = cursor.fetchall()

        # Récupérer les présences pour ce cours
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
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
                WHERE nom_cours = %s AND filiere = %s
                AND date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
                ORDER BY date_upload DESC
            """, (nom_cours, filiere_cours))
        else:
            # Si les colonnes n'existent pas encore, utiliser course_id (compatibilité)
            cursor.execute("""
                SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload, visible
                FROM documents
                WHERE course_id = %s
                ORDER BY date_upload DESC
            """, (course_id,))
        documents = cursor.fetchall()

        # Récupérer les notes (si la table existe)
        notes_data = {}
        try:
            cursor.execute("""
                SELECT etudiant_id, nom_cours, CC1, CC2, Participation, Examen
                FROM notes
                WHERE nom_cours = %s
            """, (course['nom_cours'],))
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
            
            conn.commit()
        except Exception as e:
            print(f"Erreur création tables: {e}")
            pass

        # Récupérer les lectures
        cursor.execute("""
            SELECT id, titre, description, contenu, date_seance, ordre
            FROM lectures
            WHERE course_id = %s
            ORDER BY ordre, date_seance, created_at
        """, (course_id,))
        lectures = cursor.fetchall()

        # Récupérer les exams
        cursor.execute("""
            SELECT id, type_examen, titre, date_examen, coefficient, description
            FROM exams
            WHERE course_id = %s
            ORDER BY date_examen DESC
        """, (course_id,))
        exams = cursor.fetchall()

        # Récupérer les assignments
        cursor.execute("""
            SELECT id, titre, description, date_publication, date_limite, type_assignment, fichier_corrige
            FROM assignments
            WHERE course_id = %s
            ORDER BY date_limite DESC
        """, (course_id,))
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
        
        professeur_id = session['user_id']
        
        # Gérer les deux formats : liste ou dictionnaire
        if isinstance(presences, dict):
            # Format dictionnaire : {etudiant_id: {statut: ..., commentaire: ...}}
            for etudiant_id, presence_info in presences.items():
                statut = presence_info.get('statut', 'unspecified')
                commentaire = presence_info.get('commentaire', '')
                
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE statut = %s, commentaire = %s, professeur_id = %s, updated_at = NOW()
                """, (etudiant_id, course_id, professeur_id, date, statut, commentaire,
                      statut, commentaire, professeur_id))
        else:
            # Format liste : [{'etudiant_id': ..., 'statut': ..., ...}, ...]
            for p in presences:
                cursor.execute("""
                    INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE statut = %s, commentaire = %s, professeur_id = %s, updated_at = NOW()
                """, (p.get('etudiant_id') or p.get('etudiantId'), course_id, professeur_id, date, 
                      p.get('statut', 'unspecified'), p.get('commentaire', ''),
                      p.get('statut', 'unspecified'), p.get('commentaire', ''), professeur_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Présences enregistrées'})
    except Exception as e:
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
            INSERT INTO lectures (course_id, titre, description, contenu, date_seance, ordre)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('titre'), data.get('description'), data.get('contenu'), 
              data.get('date_seance'), data.get('ordre', 0)))
        
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
            INSERT INTO exams (course_id, type_examen, titre, date_examen, coefficient, description)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('type_examen'), data.get('titre'), data.get('date_examen'),
              data.get('coefficient', 1.0), data.get('description')))
        
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
            INSERT INTO assignments (course_id, titre, description, date_publication, date_limite, type_assignment)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (course_id, data.get('titre'), data.get('description'), data.get('date_publication'),
              data.get('date_limite'), data.get('type_assignment', 'devoir')))
        
        get_db_connection().commit()
        get_db_connection().close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

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
                    WHERE id = %s
                """, (cc1, cc2, participation, examen, professeur_id, existing['id']))
            elif semestre_exists:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
                    WHERE id = %s
                """, (cc1, cc2, participation, examen, existing['id']))
            elif professeur_id_exists and professeur_id:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s, professeur_id = %s
                    WHERE id = %s
                """, (cc1, cc2, participation, examen, professeur_id, existing['id']))
            else:
                cursor.execute("""
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
                    WHERE id = %s
                """, (cc1, cc2, participation, examen, existing['id']))
        else:
            # Insérer une nouvelle note
            if semestre_exists and professeur_id_exists and professeur_id:
                cursor.execute("""
                    INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 0)
                """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen))
            elif semestre_exists:
                cursor.execute("""
                    INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, 0)
                """, (etudiant_id, nom_cours, cc1, cc2, participation, examen))
            elif professeur_id_exists and professeur_id:
                if visible_exists:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen, visible)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
                    """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen))
                else:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, professeur_id, nom_cours, CC1, CC2, Participation, Examen)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (etudiant_id, professeur_id, nom_cours, cc1, cc2, participation, examen))
            else:
                if visible_exists:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, visible)
                        VALUES (%s, %s, %s, %s, %s, %s, 0)
                    """, (etudiant_id, nom_cours, cc1, cc2, participation, examen))
                else:
                    cursor.execute("""
                        INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (etudiant_id, nom_cours, cc1, cc2, participation, examen))
        
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
                WHERE id = %s
            """, (data.get('note'), data.get('coefficient', 1.0), 
                  datetime.now().strftime('%Y-%m-%d'), professeur_id, existing[0]))
        else:
            # Insérer une nouvelle note
            cursor.execute("""
                INSERT INTO gradebook (course_id, etudiant_id, professeur_id, type_note, note, coefficient, date_note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (course_id, data.get('etudiant_id'), professeur_id, data.get('type_note'), data.get('note'),
                  data.get('coefficient', 1.0), datetime.now().strftime('%Y-%m-%d')))
        
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
                WHERE course_id = %s AND etudiant_id = %s AND type_note = %s
            """, (course_id, etudiant_id, type_note))
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
                    WHERE id = %s
                """, (float(note_value), float(coefficient), datetime.now().strftime('%Y-%m-%d'), professeur_id, existing[0]))
            else:
                # Insérer
                cursor.execute("""
                    INSERT INTO gradebook (course_id, etudiant_id, professeur_id, type_note, note, coefficient, date_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (course_id, etudiant_id, professeur_id, type_note, float(note_value), float(coefficient), 
                      datetime.now().strftime('%Y-%m-%d')))
        
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
            WHERE c.id = %s AND u.role = 'etudiant'
        """, (course_id,))
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
        cursor.execute("SELECT chemin_fichier FROM documents WHERE id = %s AND course_id = %s", (doc_id, course_id))
        doc = cursor.fetchone()
        
        if not doc:
            return jsonify({'success': False, 'message': 'Document non trouvé'}), 404

        # Supprimer le fichier
        import os
        file_path = os.path.join('uploads', doc['chemin_fichier'])
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Supprimer de la base
        cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# Route pour créer un compte professeur (pour l'admin)
@app.route('/admin/add_professeur', methods=['GET', 'POST'])
@login_required
@admin_required
def add_professeur():
    if request.method == 'POST':
        nom = request.form['nom'].strip()
        prenom = request.form['prenom'].strip()
        email = request.form['email'].strip()
        telephone = request.form.get('telephone', '').strip()
        specialite = request.form.get('specialite', '').strip()
        password = request.form['password']

        if not nom or not prenom or not email or not password:
            flash("Tous les champs obligatoires doivent être remplis.", "danger")
            return render_template('add_professeur.html')

        if not email.endswith('@adsclass.ne'):
            flash("L'email doit se terminer par @adsclass.ne", "danger")
            return render_template('add_professeur.html', nom=nom, prenom=prenom, email=email, telephone=telephone, specialite=specialite)

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return render_template('add_professeur.html', nom=nom, prenom=prenom, telephone=telephone, specialite=specialite)

        cursor = conn.cursor()
        # Vérifier si l'email existe déjà
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            flash("Un utilisateur avec cet email existe déjà.", "danger")
            return render_template('add_professeur.html', nom=nom, prenom=prenom, telephone=telephone, specialite=specialite)

        # Créer le compte professeur
        password_hash = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (nom, prenom, email, password, role, telephone, specialite) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (nom, prenom, email, password_hash, 'professeur', telephone, specialite)
        )
        conn.commit()
        conn.close()

        flash(f"Professeur {prenom} {nom} créé avec succès !", "success")
        return redirect(url_for('admin_dashboard'))

    return render_template('add_professeur.html')



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
            cursor.execute(
                "INSERT INTO depenses (date, description, montant) VALUES (%s, %s, %s)",
                (date, nature, montant)
            )
            conn.commit()
            flash("Dépense ajoutée avec succès.", "success")
            return redirect(url_for('admin_depenses'))

        except ValueError:
            flash("Montant invalide.", "danger")
            return redirect(url_for('admin_depenses'))

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM depenses ORDER BY date DESC")
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

    cursor = conn.cursor()
    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements")
    total_recette = cursor.fetchone()[0]
    
    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses")
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

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT 
            p.date, 
            p.observation AS description, 
            p.montant,
            'Recette' AS type,
            u.prenom, 
            u.nom
        FROM paiements p
        LEFT JOIN users u ON u.id = p.etudiant_id
        ORDER BY p.date DESC
    """)
    recettes = cursor.fetchall()

    cursor.execute("""
        SELECT 
            d.date, 
            d.description, 
            d.montant,
            'Dépense' AS type,
            NULL AS prenom,
            NULL AS nom
        FROM depenses d
        ORDER BY d.date DESC
    """)
    depenses = cursor.fetchall()

    transactions = recettes + depenses
    transactions = sorted(transactions, key=lambda t: t['date'], reverse=True)

    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements")
    total_recette = cursor.fetchone()['IFNULL(SUM(montant), 0)']
    
    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses")
    total_depense = cursor.fetchone()['IFNULL(SUM(montant), 0)']

    conn.close()

    return render_template(
        "admin_finance.html",
        transactions=transactions,
        total_recette=total_recette,
        total_depense=total_depense
    )


# --- API: Finance live summary ---
@app.route('/admin/api/finance/summary')
@login_required
@admin_required
def api_finance_summary():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "db_connection_failed"}), 500

    cursor = conn.cursor()
    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements")
    total_recette = cursor.fetchone()[0]

    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM depenses")
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


@app.route('/admin/etudiants/paiements')
@login_required
@admin_required
def etudiants_paiements():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    cursor = conn.cursor(dictionary=True)
    # Liste enrichie des étudiants avec filière, niveau et stats de paiement
    cursor.execute("""
        SELECT 
            u.id, u.prenom, u.nom, u.email, 
            u.filiere, u.niveau,
            COALESCE(SUM(p.montant), 0) AS total_paye,
            COUNT(p.id) AS nombre_paiements,
            MAX(p.date) AS dernier_paiement
        FROM users u
        LEFT JOIN paiements p ON p.etudiant_id = u.id
        WHERE u.role = 'etudiant'
        GROUP BY u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau
        ORDER BY u.filiere ASC, u.niveau ASC, u.nom ASC, u.prenom ASC
    """)
    etudiants = cursor.fetchall()

    # Regrouper par (filiere, niveau) pour affichage professionnel
    etudiants_groupes = {}
    for e in etudiants:
        filiere = e.get('filiere') or 'Non défini'
        niveau = e.get('niveau') or 'Non défini'
        key = (filiere, niveau)
        if key not in etudiants_groupes:
            etudiants_groupes[key] = []
        etudiants_groupes[key].append(e)

    cursor.execute("""
        SELECT p.date, u.prenom, u.nom, p.observation AS description, p.montant, 'Recette' AS type
        FROM paiements p
        JOIN users u ON p.etudiant_id = u.id
        ORDER BY p.date DESC
    """)
    transactions = cursor.fetchall()

    conn.close()

    return render_template(
        'etudiants_paiements.html',
        etudiants=etudiants,
        etudiants_groupes=etudiants_groupes,
        transactions=transactions
    )

@app.route('/admin/api/etudiant/<int:etudiant_id>/paiements', methods=['GET', 'POST'])
@login_required
@admin_required
def api_paiements_etudiant(etudiant_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500

    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        data = request.json
        try:
            date = data['date']
            montant = float(data['montant'])
            moyen = data.get('moyen', '')
            observation = data.get('observation', '')
        except (KeyError, ValueError):
            conn.close()
            return jsonify({'error': 'Données invalides'}), 400

        cursor.execute(
            "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation) VALUES (%s, %s, %s, %s, %s)",
            (etudiant_id, date, montant, moyen, observation)
        )
        conn.commit()

    cursor.execute(
        "SELECT id, date, montant, moyen, observation FROM paiements WHERE etudiant_id = %s ORDER BY date DESC",
        (etudiant_id,)
    )
    paiements = cursor.fetchall()
    conn.close()

    return jsonify(paiements)


@app.route('/admin/etudiant/<int:etudiant_id>/paiements', methods=['GET', 'POST'])
@login_required
@admin_required
def paiements_etudiant_page(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('etudiants_paiements'))
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT prenom, nom FROM users WHERE id = %s", (etudiant_id,))
    etudiant = cursor.fetchone()
    if etudiant is None:
        conn.close()
        flash("Étudiant introuvable.", "danger")
        return redirect(url_for('etudiants_paiements'))

    if request.method == 'POST':
        date = request.form['date']
        try:
            montant = float(request.form['montant'])
        except ValueError:
            flash("Montant invalide.", "danger")
            conn.close()
            return redirect(url_for('paiements_etudiant_page', etudiant_id=etudiant_id))
        moyen = request.form['moyen']
        observation = request.form['observation']

        cursor.execute(
            "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation) VALUES (%s, %s, %s, %s, %s)",
            (etudiant_id, date, montant, moyen, observation)
        )
        conn.commit()
        flash("Paiement ajouté.", "success")

    cursor.execute(
        "SELECT id, date, montant, moyen, observation FROM paiements WHERE etudiant_id = %s ORDER BY date DESC",
        (etudiant_id,)
    )
    paiements = cursor.fetchall()

    montant_du = 60000  # À adapter selon ta logique
    cursor.execute(
        "SELECT IFNULL(SUM(montant), 0) FROM paiements WHERE etudiant_id = %s",
        (etudiant_id,)
    )
    total_paye = cursor.fetchone()['IFNULL(SUM(montant), 0)']
    solde = montant_du - total_paye
    a_jour = total_paye >= montant_du

    conn.close()

    return render_template(
        'paiements_etudiant.html',
        paiements=paiements,
        etudiant=etudiant,
        total_paye=total_paye,
        montant_du=montant_du,
        solde=solde,
        a_jour=a_jour,
        etudiant_id=etudiant_id
    )

@app.route('/admin/etudiant/<int:etudiant_id>/paiements/ajouter', methods=['GET', 'POST'])
@login_required
@admin_required
def ajouter_paiement(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('etudiants_paiements'))
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (etudiant_id,))
    etudiant = cursor.fetchone()

    if not etudiant:
        conn.close()
        flash("Étudiant introuvable.", "danger")
        return redirect(url_for('etudiants_paiements'))

    if request.method == 'POST':
        date = request.form['date']
        montant = request.form['montant']
        moyen = request.form.get('moyen', '').strip()
        observation = request.form.get('observation', '').strip()

        if not date or not montant:
            flash("La date et le montant sont obligatoires.", "danger")
            return render_template('ajouter_paiement.html', etudiant=etudiant)

        try:
            montant_float = float(montant)
        except ValueError:
            flash("Montant invalide.", "danger")
            return render_template('ajouter_paiement.html', etudiant=etudiant)

        cursor.execute(
            "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation) VALUES (%s, %s, %s, %s, %s)",
            (etudiant_id, date, montant_float, moyen, observation)
        )
        conn.commit()
        conn.close()
        flash("Paiement enregistré avec succès.", "success")
        return redirect(url_for('paiements_etudiant_page', etudiant_id=etudiant_id))

    conn.close()
    return render_template('ajouter_paiement.html', etudiant=etudiant)


@app.route('/admin/etudiant/<int:etudiant_id>/paiements/export')
@login_required
@admin_required
def export_paiements_csv(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('etudiants_paiements'))
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT date, montant, moyen, observation FROM paiements WHERE etudiant_id = %s ORDER BY date DESC",
        (etudiant_id,)
    )
    paiements = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Montant', 'Moyen', 'Observation'])

    for p in paiements:
        writer.writerow([p['date'], p['montant'], p['moyen'], p['observation']])

    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'paiements_etudiant_{etudiant_id}.csv'
    )


# Récupérer un paiement spécifique (GET)
@app.route('/admin/api/etudiant/<int:etudiant_id>/paiements/<int:paiement_id>', methods=['GET'])
@login_required
@admin_required
def api_get_paiement(etudiant_id, paiement_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, date, montant, moyen, observation FROM paiements WHERE id = %s AND etudiant_id = %s",
        (paiement_id, etudiant_id)
    )
    paiement = cursor.fetchone()
    conn.close()
    if paiement is None:
        return jsonify({'error': 'Paiement non trouvé'}), 404
    return jsonify(paiement)

# Modifier un paiement (PUT)
@app.route('/admin/api/etudiant/<int:etudiant_id>/paiements/<int:paiement_id>', methods=['PUT'])
@login_required
@admin_required
def api_modifier_paiement(etudiant_id, paiement_id):
    data = request.json
    if not data:
        return jsonify({'error': 'Aucune donnée envoyée'}), 400
    date = data.get('date')
    montant = data.get('montant')
    moyen = data.get('moyen', '')
    observation = data.get('observation', '')
    if not date or montant is None:
        return jsonify({'error': 'Date et montant requis'}), 400
    try:
        montant = float(montant)
    except ValueError:
        return jsonify({'error': 'Montant invalide'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE paiements SET date = %s, montant = %s, moyen = %s, observation = %s WHERE id = %s AND etudiant_id = %s",
        (date, montant, moyen, observation, paiement_id, etudiant_id)
    )
    conn.commit()
    updated_rows = cursor.rowcount
    conn.close()
    if updated_rows == 0:
        return jsonify({'error': 'Paiement non trouvé ou non modifié'}), 404
    return jsonify({'message': 'Paiement modifié avec succès'})

# Supprimer un paiement (DELETE)
@app.route('/admin/api/etudiant/<int:etudiant_id>/paiements/<int:paiement_id>', methods=['DELETE'])
@login_required
@admin_required
def api_supprimer_paiement(etudiant_id, paiement_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM paiements WHERE id = %s AND etudiant_id = %s",
        (paiement_id, etudiant_id)
    )
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    if deleted_rows == 0:
        return jsonify({'error': 'Paiement non trouvé'}), 404
    return jsonify({'message': 'Paiement supprimé avec succès'})


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
    
    cursor = conn.cursor()
    
    # Statistiques des utilisateurs
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='etudiant'")
    count_etudiants = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='professeur'")
    count_professeurs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    count_admins = cursor.fetchone()[0]
    
    # Statistiques financières
    cursor.execute("SELECT IFNULL(SUM(montant), 0) FROM paiements")
    total_recettes = cursor.fetchone()[0]
    
    cursor.execute("SELECT IFNULL(SUM(ABS(montant)), 0) FROM depenses")
    total_depenses = cursor.fetchone()[0]
    benefice_net = total_recettes - total_depenses
    
    # Statistiques des cours
    cursor.execute("SELECT COUNT(*) FROM courses")
    count_courses = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM courses WHERE professeur_id IS NOT NULL AND professeur_nom != ''")
    courses_with_prof = cursor.fetchone()[0]
    
    # Statistiques des paiements
    cursor.execute("""
        SELECT COUNT(DISTINCT etudiant_id) FROM paiements 
        WHERE etudiant_id IN (
            SELECT id FROM users WHERE role = 'etudiant'
        ) AND etudiant_id IN (
            SELECT etudiant_id FROM paiements 
            GROUP BY etudiant_id 
            HAVING SUM(montant) >= 60000
        )
    """)
    etudiants_a_jour = cursor.fetchone()[0]
    
    # Répartition par filière
    cursor.execute("""
        SELECT filiere, COUNT(*) as count 
        FROM users 
        WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != ''
        GROUP BY filiere 
        ORDER BY count DESC
    """)
    filieres_stats = cursor.fetchall()
    
    # Évolution mensuelle des paiements (6 derniers mois)
    cursor.execute("""
        SELECT 
            DATE_FORMAT(date, '%%Y-%%m') as mois,
            SUM(montant) as total,
            COUNT(*) as nombre_paiements
        FROM paiements 
        WHERE date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
        GROUP BY DATE_FORMAT(date, '%%Y-%%m')
        ORDER BY mois DESC
        LIMIT 6
    """)
    paiements_mensuels = cursor.fetchall()
    
    # Top 5 des dépenses récentes
    cursor.execute("""
        SELECT description, ABS(montant) as montant, date
        FROM depenses 
        ORDER BY ABS(montant) DESC, date DESC
        LIMIT 5
    """)
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
        "SELECT prenom, nom, email, filiere FROM users WHERE role='etudiant' ORDER BY nom ASC"
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
        ORDER BY filiere, niveau, nom ASC
    """
    
    cursor.execute(query)
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
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))

    cursor = conn.cursor(dictionary=True)
    
    # Définir les 5 niveaux standards
    niveaux_standards = ['L1', 'L2', 'L3', 'M1', 'M2']
    
    # Récupérer toutes les filières distinctes
    cursor.execute("""
        SELECT DISTINCT filiere 
        FROM users 
        WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != ''
        ORDER BY filiere ASC
    """)
    filieres = cursor.fetchall()

    # Récupérer le nombre d'étudiants par filière et niveau
    cursor.execute("""
        SELECT filiere, niveau, COUNT(*) as count
            FROM users 
        WHERE role = 'etudiant' AND filiere IS NOT NULL AND filiere != ''
        AND niveau IS NOT NULL AND niveau != ''
        GROUP BY filiere, niveau
    """)
    classes_data = cursor.fetchall()
    
    # Créer un dictionnaire pour accéder rapidement aux counts
    counts_dict = {}
    for row in classes_data:
        filiere = row['filiere']
        niveau = row['niveau']
        # Normaliser le niveau (prendre juste L1, L2, etc.)
        niveau_normalise = niveau.split()[0] if niveau else ""
        key = (filiere, niveau_normalise)
        counts_dict[key] = row['count']

    # Fonction pour générer le nom de classe (L1-IA, L2-IA, etc.)
    def generer_nom_classe(niveau, filiere):
        # Extraire le numéro du niveau (L1, L2, L3, M1, M2, etc.)
        niveau_abbrev = niveau.split()[0] if niveau else ""
        
        # Mapping des filières vers leurs abréviations
        filiere_abbrev_map = {
            'Intelligence Artificielle': 'IA',
            'IA': 'IA',
            'Comptabilité Contrôle Audit': 'CCA',
            'CCA': 'CCA',
            'Finance': 'FINANCE',
            'Finance et Gestion': 'FINANCE',
            'Médecine': 'MEDS',
            'MEDS': 'MEDS',
            'Marketing': 'MARKETING',
            'Marketing Digital': 'MARKETING'
        }
        
        # Utiliser le mapping ou prendre les 3 premières lettres en majuscules
        filiere_abbrev = filiere_abbrev_map.get(filiere, filiere.upper()[:8] if filiere else "")
        
        return f"{niveau_abbrev}-{filiere_abbrev}"

    # Organiser les classes par filière avec les 5 niveaux pour chaque filière
    # Les niveaux seront dans l'ordre : L1, L2, L3, M1, M2
    classes_par_filiere = {}
    
    for filiere_row in filieres:
        filiere = filiere_row['filiere']
        classes_par_filiere[filiere] = []
        
        # Pour chaque niveau standard (dans l'ordre L1, L2, L3, M1, M2), créer une entrée
        for niveau_abbrev in niveaux_standards:
            # Déterminer les variantes textuelles pour correspondance flexible
            variantes = {
                'L1': ['L1', 'Licence 1', 'LICENCE 1'],
                'L2': ['L2', 'Licence 2', 'LICENCE 2'],
                'L3': ['L3', 'Licence 3', 'LICENCE 3'],
                'M1': ['M1', 'Master 1', 'MASTER 1'],
                'M2': ['M2', 'Master 2', 'MASTER 2'],
            }.get(niveau_abbrev, [niveau_abbrev])
            
            # Construire les patterns SQL
            patterns = []
            for v in variantes:
                v_clean = v.strip()
                patterns.append(v_clean)               # égalité stricte
                patterns.append(f"{v_clean} %")        # commence par
                patterns.append(f"% {v_clean}%")       # contient avec espace
            
            # Compter de manière flexible via la base
            # On utilise TRIM(niveau) = %s OR TRIM(niveau) LIKE %s ... pour toutes les variantes
            conditions = " OR ".join(["TRIM(niveau) = %s"] + ["TRIM(niveau) LIKE %s"] * (len(patterns) - 1))
            sql_count = f"""
                SELECT COUNT(*) as count
                FROM users
                WHERE role = 'etudiant'
                  AND filiere = %s
                  AND niveau IS NOT NULL AND niveau != ''
                  AND ({conditions})
            """
            params = [filiere] + patterns
            cursor.execute(sql_count, tuple(params))
            count_row = cursor.fetchone() or {'count': 0}
            count = count_row['count'] or 0
            
            # Niveau complet affiché: on garde l'abréviation comme libellé court
            niveau_complet = niveau_abbrev
            
            nom_classe = generer_nom_classe(niveau_complet, filiere)
            
            classes_par_filiere[filiere].append({
                'nom_classe': nom_classe,
                'niveau': niveau_complet,
                'count': count
        })

    conn.close()
    return render_template("admin_classes.html", classes_par_filiere=classes_par_filiere)

@app.route('/admin/classes/<filiere>/<niveau>')
@login_required
@admin_required
def admin_class_details(filiere, niveau):
    from urllib.parse import unquote
    
    # Décoder les paramètres URL
    filiere = unquote(filiere)
    niveau = unquote(niveau)
    
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_classes'))

    cursor = conn.cursor(dictionary=True)
    
    # Fonction pour générer le nom de classe
    def generer_nom_classe(niveau, filiere):
        niveau_abbrev = niveau.split()[0] if niveau else ""
        filiere_abbrev_map = {
            'Intelligence Artificielle': 'IA',
            'IA': 'IA',
            'Comptabilité Contrôle Audit': 'CCA',
            'CCA': 'CCA',
            'Finance': 'FINANCE',
            'Finance et Gestion': 'FINANCE',
            'Médecine': 'MEDS',
            'MEDS': 'MEDS',
            'Marketing': 'MARKETING',
            'Marketing Digital': 'MARKETING'
        }
        filiere_abbrev = filiere_abbrev_map.get(filiere, filiere.upper()[:8] if filiere else "")
        return f"{niveau_abbrev}-{filiere_abbrev}"
    
    nom_classe = generer_nom_classe(niveau, filiere)
    
    # Récupérer les étudiants de cette classe avec correspondance flexible du niveau
    # Ex: "L1" ~ "Licence 1", "M2" ~ "Master 2"
    niveau_clean = niveau.strip() if niveau else ""
    niveau_abbrev = niveau_clean.split()[0] if niveau_clean else ""
    # Variantes par niveau abrégé
    variantes_map = {
        'L1': ['L1', 'Licence 1', 'LICENCE 1'],
        'L2': ['L2', 'Licence 2', 'LICENCE 2'],
        'L3': ['L3', 'Licence 3', 'LICENCE 3'],
        'M1': ['M1', 'Master 1', 'MASTER 1'],
        'M2': ['M2', 'Master 2', 'MASTER 2'],
    }
    variantes = variantes_map.get(niveau_abbrev.upper(), [niveau_clean or niveau_abbrev])
    # Construire patterns (égalité stricte + débute par + contient)
    patterns = []
    for v in variantes:
        v_clean = v.strip()
        if not v_clean:
            continue
        patterns.append(v_clean)            # égalité stricte
        patterns.append(f"{v_clean} %")     # commence par
        patterns.append(f"% {v_clean}%")    # contient avec espace
        patterns.append(f"%{v_clean}%")     # contient partout
    
    # Vérifier si la colonne 'classe' existe
    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    classe_exists = cursor.fetchone() is not None
    
    # Construire la condition SQL flexible pour toutes les variantes
    if patterns:
        # 1 égalité + (len(patterns)-1) LIKE, mais on a plusieurs variantes: on simplifie à uniquement LIKE sur tous patterns et exact match sur premières variantes
        conditions = " OR ".join(["TRIM(niveau) = %s"] * len(variantes) + ["TRIM(niveau) LIKE %s"] * len(patterns))
        params_base = [v.strip() for v in variantes] + patterns
    else:
        conditions = "TRIM(niveau) = %s"
        params_base = [niveau_clean or niveau_abbrev]
    
    if classe_exists:
        sql = f"""
            SELECT id, prenom, nom, email, telephone, filiere, niveau, classe
            FROM users 
            WHERE role = 'etudiant' 
              AND filiere = %s 
              AND niveau IS NOT NULL AND niveau != ''
              AND (
                {conditions}
                OR classe = %s
              )
            ORDER BY nom, prenom ASC
        """
        params = [filiere] + params_base + [nom_classe]
    else:
        sql = f"""
            SELECT id, prenom, nom, email, telephone, filiere, niveau
            FROM users 
            WHERE role = 'etudiant' 
              AND filiere = %s 
              AND niveau IS NOT NULL AND niveau != ''
              AND (
                {conditions}
              )
            ORDER BY nom, prenom ASC
        """
        params = [filiere] + params_base
    
    cursor.execute(sql, tuple(params))
    
    etudiants = cursor.fetchall()
    
    conn.close()
    
    return render_template("admin_class_details.html", 
                         etudiants=etudiants,
                         filiere=filiere,
                         niveau=niveau,
                         nom_classe=nom_classe,
                         classe_exists=classe_exists)


@app.route('/admin/api/professeurs')
@login_required
@admin_required
def api_professeurs():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Erreur de connexion à la base de données'}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT prenom, nom, email FROM users WHERE role='professeur' ORDER BY nom ASC"
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
        "SELECT prenom, nom, email FROM users WHERE role='admin' ORDER BY nom ASC"
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

    prenom = session.get('prenom', 'Étudiant')
    nom = session.get('nom', '')
    email = session.get('user_email', 'non renseigné')
    filiere = session.get('filiere', '---')
    photo_filename = session.get('photo_filename')

    return render_template('profile.html',
                           prenom=prenom,
                           nom=nom,
                           email=email,
                           filiere=filiere,
                           photo_filename=photo_filename)

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
        WHERE id = %s
    """, (user_id,))
    student_data = cursor.fetchone()
    conn.close()

    if not student_data:
        flash("Données étudiant non trouvées.", "danger")
        return redirect(url_for('student_dashboard'))

    # Générer un ID étudiant unique basé sur l'ID utilisateur
    student_id = f"{user_id:06d}ADSCLASS"
    
    # Calculer la date d'expiration (2 ans à partir de maintenant)
    from datetime import datetime, timedelta
    expiry_date = datetime.now() + timedelta(days=730)  # 2 ans
    
    return render_template('student_card.html',
                           student_data=student_data,
                           student_id=student_id,
                           expiry_date=expiry_date)


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

@app.route('/admin/etudiant/paiement/<int:paiement_id>/recu')
@login_required
def imprimer_recu(paiement_id):
    try:
        if session.get('role') != 'admin':
            flash("Accès refusé.", "danger")
            return redirect(url_for('student_dashboard'))

        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('etudiants_paiements'))
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT p.*, u.prenom, u.nom FROM paiements p JOIN users u ON p.etudiant_id = u.id WHERE p.id = %s",
            (paiement_id,)
        )
        paiement = cursor.fetchone()
        conn.close()

        if paiement is None:
            flash("Paiement introuvable.", "danger")
            return redirect(url_for('etudiants_paiements'))

        # Préparer les données pour le template avec gestion d'erreurs
        from datetime import datetime
        date_generation = datetime.now().strftime('%d/%m/%Y à %H:%M')

        # Fonction simple pour convertir en lettres (basique)
        def nombre_en_lettres(montant):
            try:
                montant = float(montant) if montant else 0
                if montant == 0:
                    return "zéro franc CFA"
                elif montant < 1000:
                    return f"{int(montant)} francs CFA"
                elif montant < 1000000:
                    milliers = int(montant // 1000)
                    reste = int(montant % 1000)
                    if reste == 0:
                        return f"{milliers} mille francs CFA"
                    else:
                        return f"{milliers} mille {reste} francs CFA"
                else:
                    return f"{int(montant)} francs CFA"
            except:
                return "Montant non disponible"

        montant_en_lettres = nombre_en_lettres(paiement['montant'] if paiement['montant'] else 0)

        # paiement est déjà un dict avec cursor(dictionary=True)
        paiement_dict = paiement

        # Valeurs par défaut pour éviter les erreurs
        paiement_dict['prenom'] = paiement_dict.get('prenom', 'Prénom')
        paiement_dict['nom'] = paiement_dict.get('nom', 'Nom')
        paiement_dict['montant'] = paiement_dict.get('montant', 0)
        paiement_dict['date'] = paiement_dict.get('date', 'Date non disponible')
        paiement_dict['moyen'] = paiement_dict.get('moyen', 'Non spécifié')
        paiement_dict['observation'] = paiement_dict.get('observation', 'Aucune observation')

        # Passe le paiement et les données supplémentaires à la page du reçu
        return render_template('recu_paiement.html',
                             paiement=paiement_dict,
                             date_generation=date_generation,
                             montant_en_lettres=montant_en_lettres)

    except Exception as e:
        # Log l'erreur pour le débogage
        print(f"Erreur dans imprimer_recu: {e}")
        flash("Erreur lors de la génération du reçu.", "danger")
        return redirect(url_for('etudiants_paiements'))

# Route pour reçu individuel ultra-professionnel (basée sur le reçu annuel qui fonctionne)
@app.route('/admin/etudiant/paiement/<int:paiement_id>/recu/pro')
@login_required
def imprimer_recu_pro(paiement_id):
    try:
        if session.get('role') != 'admin':
            return "Accès refusé", 403

        conn = get_db_connection()
        if not conn:
            return "Erreur de connexion à la base de données", 500

        cursor = conn.cursor(dictionary=True)
        # Utiliser une approche similaire au reçu annuel qui fonctionne
        # D'abord récupérer le paiement
        cursor.execute(
            "SELECT * FROM paiements WHERE id = %s",
            (paiement_id,)
        )
        paiement_row = cursor.fetchone()

        if paiement_row is None:
            conn.close()
            return f"Paiement avec ID {paiement_id} introuvable", 404

        # Ensuite récupérer l'étudiant associé
        cursor.execute(
            "SELECT * FROM users WHERE id = %s AND role = 'etudiant'",
            (paiement_row['etudiant_id'],)
        )
        etudiant_row = cursor.fetchone()

        conn.close()

        if etudiant_row is None:
            return f"Étudiant associé au paiement introuvable", 404

        # Préparer les données comme pour le reçu annuel
        from datetime import datetime
        import hashlib

        date_generation = datetime.now().strftime('%d/%m/%Y à %H:%M')

        # paiement_row et etudiant_row sont déjà des dicts avec cursor(dictionary=True)
        paiement_dict = paiement_row
        etudiant_dict = etudiant_row

        # Combiner les données paiement + étudiant
        paiement_complet = {
            # Données du paiement
            'id': paiement_dict.get('id', paiement_id),
            'montant': paiement_dict.get('montant', 0),
            'date': paiement_dict.get('date', '2026-08-05'),
            'moyen': paiement_dict.get('moyen', 'Non spécifié'),
            'observation': paiement_dict.get('observation', ''),
            'etudiant_id': paiement_dict.get('etudiant_id', 0),

            # Données de l'étudiant
            'prenom': etudiant_dict.get('prenom', 'Prénom'),
            'nom': etudiant_dict.get('nom', 'Nom'),
            'email': etudiant_dict.get('email', 'email@example.com'),
            'telephone': etudiant_dict.get('telephone', 'Non renseigné')
        }

        # Montant en lettres simplifié
        try:
            montant = float(paiement_complet['montant']) if paiement_complet['montant'] else 0
            if montant == 0:
                montant_en_lettres = "zéro franc CFA"
            elif montant < 1000:
                montant_en_lettres = f"{int(montant)} francs CFA"
            elif montant < 1000000:
                milliers = int(montant // 1000)
                reste = int(montant % 1000)
                if reste == 0:
                    montant_en_lettres = f"{milliers} mille francs CFA"
                else:
                    montant_en_lettres = f"{milliers} mille {reste} francs CFA"
            else:
                montant_en_lettres = f"{int(montant)} francs CFA"
        except:
            montant_en_lettres = "Montant non disponible"

        # Code de sécurité unique
        security_string = f"{paiement_id}{paiement_complet['montant']}{date_generation}"
        security_code = hashlib.md5(security_string.encode()).hexdigest()[:8].upper()

        # Utiliser le même template que le reçu annuel
        return render_template('recu_paiement_pro.html',
                             paiement=paiement_complet,
                             date_generation=date_generation,
                             montant_en_lettres=montant_en_lettres,
                             security_code=security_code,
                             type_recu='individuel')

    except Exception as e:
        # Log détaillé de l'erreur
        import traceback
        error_details = traceback.format_exc()
        print(f"Erreur complète dans imprimer_recu_pro: {error_details}")
        return f"Erreur détaillée: {str(e)}<br><pre>{error_details}</pre>", 500

# Route pour reçu annuel complet
@app.route('/admin/etudiant/<int:etudiant_id>/recu/annuel')
@login_required
def imprimer_recu_annuel(etudiant_id):
    try:
        if session.get('role') != 'admin':
            return "Accès refusé", 403

        conn = get_db_connection()
        if not conn:
            return "Erreur de connexion à la base de données", 500

        cursor = conn.cursor(dictionary=True)
        # Récupérer les informations de l'étudiant
        cursor.execute(
            "SELECT * FROM users WHERE id = %s AND role = 'etudiant'",
            (etudiant_id,)
        )
        etudiant = cursor.fetchone()

        if etudiant is None:
            conn.close()
            return "Étudiant introuvable", 404

        # Récupérer tous les paiements de l'étudiant pour l'année en cours
        from datetime import datetime
        annee_courante = datetime.now().year

        cursor.execute(
            """SELECT * FROM paiements
               WHERE etudiant_id = %s
               AND YEAR(date) = %s
               ORDER BY date ASC""",
            (etudiant_id, annee_courante)
        )
        paiements = cursor.fetchall()

        conn.close()

        # Préparer les données pour le template
        import hashlib

        date_generation = datetime.now().strftime('%d/%m/%Y à %H:%M')

        # etudiant et paiements sont déjà des dicts avec cursor(dictionary=True)
        etudiant_dict = etudiant
        etudiant_dict['prenom'] = etudiant_dict.get('prenom', 'Prénom')
        etudiant_dict['nom'] = etudiant_dict.get('nom', 'Nom')
        etudiant_dict['email'] = etudiant_dict.get('email', 'email@example.com')
        etudiant_dict['telephone'] = etudiant_dict.get('telephone', 'Non renseigné')

        # paiements est déjà une liste de dicts
        paiements_list = paiements

        # Calculer les statistiques
        total_paye = sum(p['montant'] for p in paiements_list)
        nombre_paiements = len(paiements_list)

        # Moyenne par paiement
        moyenne_paiement = total_paye / nombre_paiements if nombre_paiements > 0 else 0

        # Répartition par moyen de paiement
        moyens_stats = {}
        for p in paiements_list:
            moyen = p.get('moyen', 'Non spécifié')
            moyens_stats[moyen] = moyens_stats.get(moyen, 0) + p['montant']

        # Code de sécurité unique pour le reçu annuel
        security_string = f"{etudiant_id}{total_paye}{nombre_paiements}{annee_courante}"
        security_code = hashlib.md5(security_string.encode()).hexdigest()[:8].upper()

        return render_template('recu_paiement_pro.html',
                             etudiant=etudiant_dict,
                             paiements=paiements_list,
                             total_paye=total_paye,
                             nombre_paiements=nombre_paiements,
                             moyenne_paiement=moyenne_paiement,
                             moyens_stats=moyens_stats,
                             annee_courante=annee_courante,
                             date_generation=date_generation,
                             security_code=security_code,
                             type_recu='annuel')

    except Exception as e:
        return f"Erreur: {str(e)}", 500

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
        'UPDATE depenses SET date = %s, description = %s, montant = %s WHERE id = %s',
        (date, description, montant, id)
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
    cursor.execute('DELETE FROM depenses WHERE id = %s', (id,))
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
    
    # Récupérer les cours liés à cette filière et niveau
    # Joindre avec emploi_temps pour vérifier que l'étudiant est inscrit
    if niveau_exists and niveau:
        # Correspondance flexible du niveau pour gérer "L1" vs "Licence 1", etc.
        niveau_clean = niveau.strip()
        niveau_abbrev = niveau_clean.split()[0] if niveau_clean else ""
        cursor.execute("""
            SELECT DISTINCT c.nom_cours AS title, c.start, c.end 
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'etudiant' 
            AND c.filiere = %s 
            AND c.niveau IS NOT NULL AND c.niveau != ''
            AND (
                TRIM(c.niveau) = %s 
                OR TRIM(c.niveau) = %s
                OR c.niveau LIKE %s
                OR c.niveau LIKE %s
            )
            ORDER BY c.start
        """, (user_id, filiere, niveau_clean, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%'))
    else:
        # Si pas de niveau ou colonne niveau n'existe pas, filtrer uniquement par filière
        cursor.execute("""
            SELECT DISTINCT c.nom_cours AS title, c.start, c.end 
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'etudiant' 
            AND c.filiere = %s
            ORDER BY c.start
        """, (user_id, filiere))
    
    events = cursor.fetchall()
    conn.close()

    return events

def get_student_notes(user_id, cours_list):
    conn = get_db_connection()
    if not conn:
        return {}
    
    cursor = conn.cursor(dictionary=True)

    notes = {}
    for cours in cours_list:
        cursor.execute('''
            SELECT cc1, cc2, participation, examen FROM notes 
            WHERE etudiant_id = %s AND nom_cours = %s
        ''', (user_id, cours))
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
    
    # Récupérer les modules uniques (par nom_cours) pour cet étudiant
    # Chaque module apparaît une seule fois même s'il a plusieurs séances ou plusieurs entrées dans courses
    cursor.execute("""
        SELECT 
            MIN(c.id) as id,
            c.nom_cours,
            c.filiere,
            c.niveau,
            c.description,
            MIN(c.salle) as salle,
            MIN(c.heure_debut) as heure_debut,
            MIN(c.heure_fin) as heure_fin,
            MIN(c.jour_semaine) as jour_semaine,
            COUNT(DISTINCT c.id) as nb_seances
        FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant' AND c.filiere = %s
        GROUP BY c.nom_cours, c.filiere, c.niveau, c.description
        ORDER BY c.nom_cours
    """, (user_id, user_filiere))
    
    cours = cursor.fetchall()
    conn.close()
    
    # Récupérer les notes pour ces cours
    cours_noms = [c['nom_cours'] for c in cours]
    notes = get_student_notes(user_id, cours_noms)
    
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
        
        # Vérifier que l'étudiant est inscrit à ce cours et récupérer les infos du professeur
        cursor.execute("""
            SELECT c.*, 
                   prof.prenom as prof_prenom, 
                   prof.nom as prof_nom
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            LEFT JOIN emploi_temps et_prof ON c.id = et_prof.course_id AND et_prof.role = 'professeur'
            LEFT JOIN users prof ON et_prof.user_id = prof.id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'etudiant'
            LIMIT 1
        """, (course_id, user_id))
        
        course = cursor.fetchone()
        
        if not course:
            flash("Module non trouvé ou accès refusé.", "danger")
            conn.close()
            return redirect(url_for('student_courses'))

        # Récupérer les présences de cet étudiant pour ce cours
        cursor.execute("""
            SELECT date_cours, statut, commentaire
            FROM presences
            WHERE course_id = %s AND etudiant_id = %s
            ORDER BY date_cours DESC
        """, (course_id, user_id))
        presences = cursor.fetchall()

        # Récupérer les documents/ressources visibles pour ce module (par nom_cours et filiere)
        # Les documents restent disponibles pendant 5 ans
        nom_cours = course['nom_cours']
        filiere_cours = course['filiere']
        
        # Vérifier si les colonnes nom_cours et filiere existent dans documents
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
        nom_cours_exists = cursor.fetchone() is not None
        
        if nom_cours_exists:
            # Récupérer tous les documents de ce module (nom_cours + filiere) de moins de 5 ans
            cursor.execute("""
                SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload
                FROM documents
                WHERE nom_cours = %s AND filiere = %s AND visible = 1
                AND date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
                ORDER BY date_upload DESC
            """, (nom_cours, filiere_cours))
        else:
            # Si les colonnes n'existent pas encore, utiliser course_id (compatibilité)
            cursor.execute("""
                SELECT id, titre, description, nom_fichier, chemin_fichier, type_doc, date_upload
                FROM documents
                WHERE course_id = %s AND visible = 1
                ORDER BY date_upload DESC
            """, (course_id,))
        documents = cursor.fetchall()

        # Récupérer les lectures
        cursor.execute("""
            SELECT id, titre, description, contenu, date_seance, ordre
            FROM lectures
            WHERE course_id = %s
            ORDER BY ordre, date_seance
        """, (course_id,))
        lectures = cursor.fetchall()

        # Récupérer les examens
        cursor.execute("""
            SELECT id, type_examen, titre, date_examen, coefficient, description
            FROM exams
            WHERE course_id = %s
            ORDER BY date_examen
        """, (course_id,))
        exams = cursor.fetchall()

        # Récupérer les assignments
        cursor.execute("""
            SELECT id, titre, description, date_publication, date_limite, type_assignment, fichier_corrige
            FROM assignments
            WHERE course_id = %s
            ORDER BY date_publication DESC
        """, (course_id,))
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
                WHERE etudiant_id = %s AND nom_cours = %s AND visible = 1
            """, (user_id, course['nom_cours']))
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


@app.route('/admin/grades')
def admin_grades():
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_home'))
    
    cursor = conn.cursor(dictionary=True)

    # Récupérer tous les étudiants ayant le rôle 'etudiant'
    cursor.execute('''
        SELECT * FROM users
        WHERE role = 'etudiant'
        ORDER BY filiere, niveau, nom
    ''')
    etudiants = cursor.fetchall()

    # Vérifier si la colonne semestre existe
    cursor.execute("SHOW COLUMNS FROM notes LIKE 'semestre'")
    semestre_exists = cursor.fetchone() is not None
    
    # Vérifier si la colonne visible existe
    cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
    visible_exists = cursor.fetchone() is not None
    
    # Récupérer toutes les notes associées aux étudiants (incluant celles du gradebook synchronisées)
    if semestre_exists and visible_exists:
        cursor.execute('''
            SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, visible
            FROM notes
        ''')
    elif semestre_exists:
        cursor.execute('''
            SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, 0 as visible
            FROM notes
        ''')
    elif visible_exists:
        cursor.execute('''
            SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, visible
            FROM notes
        ''')
    else:
        cursor.execute('''
            SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen, 0 as visible
            FROM notes
        ''')
    notes_rows = cursor.fetchall()

    # Organiser les notes en un dictionnaire : { etudiant_id: [liste de notes (dict)] }
    notes_par_etudiant = {}
    for row in notes_rows:
        note = row  # row est déjà un dict avec cursor(dictionary=True)
        etu_id = note['etudiant_id']
        notes_par_etudiant.setdefault(etu_id, []).append(note)

    # Regrouper les étudiants par (filiere, niveau)
    groupes = {}
    for etu in etudiants:
        key = (etu['filiere'], etu['niveau'])
        groupes.setdefault(key, []).append(etu)

    conn.close()

    return render_template(
        'admin_grades.html',
        groupes=groupes,
        notes_par_etudiant=notes_par_etudiant,
        semestre_exists=semestre_exists,
        visible_exists=visible_exists
    )

@app.route('/admin/bulletin/<int:etudiant_id>')
def admin_bulletin(etudiant_id):
    """Générer le bulletin d'un étudiant avec sélection du semestre"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))
    
    # Récupérer le semestre depuis les paramètres de requête
    semestre = request.args.get('semestre', '1')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Vérifier si la colonne semestre existe, sinon l'ajouter
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'semestre'")
        semestre_exists = cursor.fetchone() is not None
        
        if not semestre_exists:
            try:
                cursor.execute("ALTER TABLE notes ADD COLUMN semestre INT DEFAULT 1")
                conn.commit()
            except Exception as e:
                print(f"Erreur lors de l'ajout de la colonne semestre: {e}")
        
        # Récupérer les informations de l'étudiant
        cursor.execute("""
            SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau, u.telephone
            FROM users u
            WHERE u.id = %s AND u.role = 'etudiant'
        """, (etudiant_id,))
        
        etudiant = cursor.fetchone()
        if not etudiant:
            flash("Étudiant non trouvé.", "error")
            return redirect(url_for('admin_grades'))
        
        # Récupérer les notes de l'étudiant pour le semestre sélectionné
        if semestre_exists:
            cursor.execute("""
                SELECT n.*
                FROM notes n
                WHERE n.etudiant_id = %s AND (n.semestre = %s OR n.semestre IS NULL)
                ORDER BY n.nom_cours
            """, (etudiant_id, int(semestre)))
        else:
            cursor.execute("""
                SELECT n.*
                FROM notes n
                WHERE n.etudiant_id = %s
                ORDER BY n.nom_cours
            """, (etudiant_id,))
        
        notes = cursor.fetchall()
        
        # Calculer les statistiques
        stats = {
            'total_cours': len(notes),
            'cours_valides': 0,
            'cours_echoues': 0,
            'moyenne_generale': 0,
            'total_credits': 0,
            'credits_valides': 0,
            'mention': 'Non défini'
        }
        
        if notes:
            total_points = 0
            total_coefficients = 0
            
            for note in notes:
                # Calculer la moyenne du cours
                moyenne_cours = (note['CC1'] + note['CC2'] + note['Participation'] + note['Examen']) / 4
                note['moyenne_cours'] = round(moyenne_cours, 2)
                
                # Déterminer si le cours est validé
                if moyenne_cours >= 10:
                    note['valide'] = True
                    stats['cours_valides'] += 1
                    stats['credits_valides'] += 1  # 1 crédit par cours validé
                else:
                    note['valide'] = False
                    stats['cours_echoues'] += 1
                
                # Calculer la moyenne générale pondérée
                coefficient = 1  # Coefficient par défaut
                total_points += moyenne_cours * coefficient
                total_coefficients += coefficient
                
                stats['total_credits'] += 1  # 1 crédit par cours
            
            if total_coefficients > 0:
                stats['moyenne_generale'] = round(total_points / total_coefficients, 2)
                
                # Déterminer la mention
                if stats['moyenne_generale'] >= 16:
                    stats['mention'] = 'Très Bien'
                elif stats['moyenne_generale'] >= 14:
                    stats['mention'] = 'Bien'
                elif stats['moyenne_generale'] >= 12:
                    stats['mention'] = 'Assez Bien'
                elif stats['moyenne_generale'] >= 10:
                    stats['mention'] = 'Passable'
                else:
                    stats['mention'] = 'Insuffisant'
        
        # Récupérer les informations de l'établissement
        annee_courante = datetime.now().year
        annee_scolaire = f"{annee_courante}-{annee_courante + 1}"
        
        etablissement = {
            'nom': 'ADS CLASS',
            'adresse': 'Niamey, Niger',
            'telephone': '+227 XX XX XX XX',
            'email': 'contact@adsclass.ne',
            'site_web': 'www.adsclass.ne',
            'directeur': 'Dr. Directeur',
            'annee_scolaire': annee_scolaire,
            'semestre': f'Semestre {semestre}'
        }
        
        conn.close()
        
        generation_date = datetime.now().strftime('%d/%m/%Y à %H:%M')
        return render_template('admin_bulletin.html', 
                             etudiant=etudiant, 
                             notes=notes, 
                             stats=stats,
                             etablissement=etablissement,
                             generation_date=generation_date,
                             semestre=semestre,
                             semestre_actuel=semestre)
        
    except Error as e:
        print(f"Erreur lors de la génération du bulletin: {e}")
        flash("Erreur lors de la génération du bulletin.", "error")
        return redirect(url_for('admin_grades'))


@app.route('/modifier_note/<int:note_id>', methods=['GET', 'POST'])
def modifier_note(note_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_grades'))
    
    cursor = conn.cursor(dictionary=True)

    # Récupérer la note existante
    cursor.execute('SELECT * FROM notes WHERE id = %s', (note_id,))
    note = cursor.fetchone()

    if note is None:
        flash("Note introuvable.")
        conn.close()
        return redirect(url_for('admin_grades'))

    if request.method == 'POST':
        CC1 = request.form['CC1']
        CC2 = request.form['CC2']
        Participation = request.form['Participation']
        Examen = request.form['Examen']

        cursor.execute('''
            UPDATE notes
            SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
            WHERE id = %s
        ''', (CC1, CC2, Participation, Examen, note_id))
        conn.commit()
        conn.close()

        flash("Note mise à jour avec succès.")
        return redirect(url_for('admin_grades'))

    conn.close()
    return render_template('modifier_note.html', note=note)


@app.route('/admin/notes/<int:note_id>/toggle_visible', methods=['POST'])
@login_required
def toggle_note_visible(note_id):
    """Rendre une note visible ou invisible pour l'étudiant"""
    try:
        if session.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403
        
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Vérifier si la colonne visible existe
        cursor.execute("SHOW COLUMNS FROM notes LIKE 'visible'")
        visible_exists = cursor.fetchone() is not None
        
        if not visible_exists:
            # Créer la colonne si elle n'existe pas
            cursor.execute("ALTER TABLE notes ADD COLUMN visible TINYINT(1) DEFAULT 0")
            conn.commit()
        
        # Récupérer l'état actuel
        cursor.execute("SELECT visible FROM notes WHERE id = %s", (note_id,))
        note = cursor.fetchone()
        
        if not note:
            conn.close()
            return jsonify({'success': False, 'message': 'Note non trouvée'}), 404
        
        # Inverser l'état
        new_visible = 1 if note['visible'] == 0 else 0
        
        # Mettre à jour
        cursor.execute("UPDATE notes SET visible = %s WHERE id = %s", (new_visible, note_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'visible': new_visible})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/saisir_notes/<int:etudiant_id>', methods=['GET', 'POST'])
def saisir_notes(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_grades'))
    
    cursor = conn.cursor(dictionary=True)
    
    # Vérifier si la colonne semestre existe, sinon l'ajouter
    cursor.execute("SHOW COLUMNS FROM notes LIKE 'semestre'")
    semestre_exists = cursor.fetchone() is not None
    
    if not semestre_exists:
        try:
            cursor.execute("ALTER TABLE notes ADD COLUMN semestre INT DEFAULT 1")
            conn.commit()
            semestre_exists = True
        except Exception as e:
            print(f"Erreur lors de l'ajout de la colonne semestre: {e}")
    
    cursor.execute('SELECT * FROM users WHERE id = %s', (etudiant_id,))
    etu = cursor.fetchone()
    if not etu:
        conn.close()
        flash("Étudiant introuvable.", "danger")
        return redirect(url_for('admin_grades'))

    if request.method == 'POST':
        nom_cours = request.form.get('nom_cours')
        cc1 = request.form.get('CC1')
        cc2 = request.form.get('CC2')
        participation = request.form.get('Participation')
        examen = request.form.get('Examen')
        semestre = request.form.get('semestre', '1')

        # Vérifier si une note existe déjà pour ce cours et ce semestre
        if semestre_exists:
            cursor.execute('''
                SELECT id FROM notes 
                WHERE etudiant_id = %s AND nom_cours = %s AND semestre = %s
            ''', (etudiant_id, nom_cours, int(semestre)))
            existing = cursor.fetchone()
            
            if existing:
                # Mise à jour
                cursor.execute('''
                    UPDATE notes 
                    SET CC1 = %s, CC2 = %s, Participation = %s, Examen = %s
                    WHERE id = %s
                ''', (cc1, cc2, participation, examen, existing['id']))
            else:
                # Insertion
                cursor.execute('''
                    INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (etudiant_id, nom_cours, cc1, cc2, participation, examen, int(semestre)))
        else:
            # Insertion sans semestre (rétrocompatibilité)
            cursor.execute('''
                INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (etudiant_id, nom_cours, cc1, cc2, participation, examen))
        
        conn.commit()
        conn.close()

        flash("Notes enregistrées avec succès pour le semestre " + semestre + ".", "success")
        return redirect(url_for('admin_grades'))

    conn.close()
    return render_template('saisir_notes.html', etudiant=etu, semestre_exists=semestre_exists)

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
    
    # Récupération des cours liés à cet étudiant (uniquement les notes visibles)
    if visible_exists:
        cursor.execute("SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s AND visible = 1", (user_id,))
    else:
        cursor.execute("SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s", (user_id,))
    cours = [row['nom_cours'] for row in cursor.fetchall()]

    notes_dict = {}
    for nom_cours in cours:
        if visible_exists:
            cursor.execute("""
                SELECT CC1, CC2, Participation, Examen 
                FROM notes 
                WHERE etudiant_id = %s AND nom_cours = %s AND visible = 1
            """, (user_id, nom_cours))
        else:
            cursor.execute("""
                SELECT CC1, CC2, Participation, Examen 
                FROM notes 
                WHERE etudiant_id = %s AND nom_cours = %s
            """, (user_id, nom_cours))
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
        cursor.execute("SELECT * FROM users WHERE id = %s", (paiement['etudiant_id'],))
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
        cursor.execute("SELECT * FROM users WHERE id = %s AND role = 'professeur'", (prof_id,))
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
                 taille_fichier, type_fichier, visible)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (course_id, nom_cours, filiere_cours, session['user_id'],
                      request.form.get('titre', filename),
                      request.form.get('description', ''),
                      filename, filepath, file_size, file_extension, 1))
            else:
                # Compatibilité avec l'ancienne structure
                cursor.execute('''
                INSERT INTO documents
                (course_id, professeur_id, titre, description, nom_fichier, chemin_fichier,
                 taille_fichier, type_fichier, visible)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (course_id, session['user_id'],
                      request.form.get('titre', filename),
                      request.form.get('description', ''),
                      filename, filepath, file_size, file_extension, 1))

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
        WHERE d.id = %s AND d.visible = 1
        ''', (document_id,))

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
        # Vérifier que l'étudiant est inscrit au cours
        cursor.execute('''
        SELECT c.* FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE c.id = %s AND et.user_id = %s AND et.role = 'etudiant'
        ''', (course_id, session['user_id']))

        course = cursor.fetchone()
        if not course:
            flash('Cours non trouvé ou accès non autorisé', 'error')
            return redirect(url_for('student_dashboard'))

        # Récupérer tous les documents du cours
        cursor.execute('''
        SELECT d.*, u.nom as prof_nom, u.prenom as prof_prenom
        FROM documents d
        JOIN users u ON d.professeur_id = u.id
        WHERE d.course_id = %s AND d.visible = 1
        ORDER BY d.date_upload DESC
        ''', (course_id,))

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
        # Récupérer la liste des professeurs
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données', 'error')
            return redirect(url_for('admin_dashboard'))
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, nom, prenom FROM users WHERE role = 'professeur' ORDER BY nom, prenom")
        professeurs = cursor.fetchall()
        conn.close()

        return render_template('admin_ajouter_cours_simple.html', professeurs=professeurs)

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

        # Essayer de trouver le professeur dans la base par nom si fourni
        professeur_id = None
        if professeur_nom:
            conn = get_db_connection()
            if not conn:
                flash('Erreur de connexion à la base de données', 'error')
                return redirect(request.url)
            
            cursor = conn.cursor()

            # Recherche par nom complet ou parties du nom
            cursor.execute("""
                SELECT id FROM users
                WHERE role = 'professeur'
                AND (LOWER(CONCAT(nom, ' ', prenom)) LIKE LOWER(%s)
                     OR LOWER(CONCAT(prenom, ' ', nom)) LIKE LOWER(%s))
                LIMIT 1
            """, (f'%{professeur_nom}%', f'%{professeur_nom}%'))

            prof = cursor.fetchone()
            if prof:
                professeur_id = prof[0]
            conn.close()

        # Insérer le cours
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données', 'error')
            return redirect(request.url)
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO courses (nom_cours, professeur_id, professeur_nom, start, end, filiere, niveau, salle,
            date_cours, jour_semaine, heure_debut, heure_fin, recurrent, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (nom_cours, professeur_id, professeur_nom, start, end, filiere, niveau, salle,
            date_cours, jour_semaine, heure_debut, heure_fin, 1, f"Cours de {nom_cours} en {filiere}"))

        course_id = cursor.lastrowid

        # Ajouter automatiquement le professeur à l'emploi du temps s'il est trouvé dans la base
        if professeur_id:
            cursor.execute('''
            INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications)
            VALUES (%s, %s, %s, %s, %s)
            ''', (professeur_id, course_id, 'professeur', 1, 1))

        # Ajouter automatiquement les étudiants de la filière
        cursor.execute('''
        SELECT id FROM users WHERE role = 'etudiant' AND filiere = %s
        ''', (filiere,))

        etudiants = cursor.fetchall()
        for etudiant in etudiants:
            cursor.execute('''
            INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications)
            VALUES (%s, %s, %s, %s, %s)
            ''', (etudiant[0], course_id, 'etudiant', 1, 1))

        conn.commit()
        conn.close()

        # Message de succès personnalisé
        message = f'Cours "{nom_cours}" créé avec succès pour le {jour_semaine} {date_cours} de {heure_debut} à {heure_fin}!'
        if professeur_nom:
            if professeur_id:
                message += f' Professeur "{professeur_nom}" trouvé et ajouté automatiquement.'
            else:
                message += f' Professeur "{professeur_nom}" enregistré (non trouvé dans la base).'
        message += f' {len(etudiants)} étudiants de {filiere} ajoutés automatiquement.'

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
        """, (filiere, niveau_complet, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%'))
        
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
            GROUP BY niveau
            ORDER BY cnt DESC
            LIMIT 1
        """, (filiere, niveau_complet, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%'))
        
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
    """Page de détails d'une classe avec liste des étudiants et marquage de présence"""
    from urllib.parse import unquote
    
    if session.get('role') != 'professeur':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))
    
    # Décoder les paramètres URL
    filiere = unquote(filiere)
    niveau = unquote(niveau)
    
    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('prof_classes'))

    cursor = conn.cursor(dictionary=True)
    
    # Vérifier que le professeur a des cours pour cette classe (filière + niveau)
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    niveau_exists = cursor.fetchone() is not None
    
    if niveau_exists:
        # Vérifier que le professeur a au moins un cours pour cette filière et niveau
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' AND et.visible = 1
            AND c.filiere = %s 
            AND c.niveau IS NOT NULL AND c.niveau != ''
            AND (
                c.niveau = %s 
                OR c.niveau LIKE %s
                OR c.niveau LIKE %s
            )
        """, (user_id, filiere, niveau, f'{niveau.split()[0] if niveau else ""}%', f'%{niveau.split()[0] if niveau else ""}%'))
    else:
        # Si la colonne niveau n'existe pas, vérifier seulement la filière
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' AND et.visible = 1
            AND c.filiere = %s
        """, (user_id, filiere))
    
    access_check = cursor.fetchone()
    if not access_check or access_check['count'] == 0:
        flash("Vous n'avez pas accès à cette classe. Vous devez avoir des cours programmés pour cette filière et niveau.", "danger")
        conn.close()
        return redirect(url_for('prof_classes'))
    
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
    
    nom_classe = generer_nom_classe(niveau, filiere)
    
    # Récupérer les étudiants de cette classe (basé sur filiere et niveau)
    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    classe_exists = cursor.fetchone() is not None
    
    select_fields = "id, prenom, nom, email, telephone, filiere, niveau"
    if classe_exists:
        select_fields += ", classe"

    # Rechercher les étudiants avec correspondance flexible du niveau
    niveau_abbrev = niveau.split()[0] if niveau else ""
    cursor.execute(f"""
        SELECT {select_fields}
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
        ORDER BY nom, prenom ASC
    """, (filiere, niveau, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%'))
    etudiants = cursor.fetchall()

    # Récupérer les cours que ce professeur enseigne dans cette filière et niveau
    # Utiliser la variable niveau_exists déjà définie plus haut
    
    if niveau_exists:
        # Si la colonne niveau existe, filtrer par filière et niveau avec correspondance flexible
        niveau_abbrev_cours = niveau.split()[0] if niveau else ""
        cursor.execute("""
            SELECT c.id, c.nom_cours, c.jour_semaine, c.heure_debut, c.heure_fin, c.niveau
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' 
            AND c.filiere = %s 
            AND c.niveau IS NOT NULL AND c.niveau != ''
            AND (
                c.niveau = %s 
                OR c.niveau = %s
                OR c.niveau LIKE %s
                OR c.niveau LIKE %s
            )
            AND et.visible = 1
            ORDER BY c.nom_cours ASC
        """, (user_id, filiere, niveau, niveau_abbrev_cours, f'{niveau_abbrev_cours}%', f'%{niveau_abbrev_cours}%'))
    else:
        # Si la colonne niveau n'existe pas, filtrer seulement par filière
        cursor.execute("""
            SELECT c.id, c.nom_cours, c.jour_semaine, c.heure_debut, c.heure_fin
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE et.user_id = %s AND et.role = 'professeur' 
            AND c.filiere = %s
            AND et.visible = 1
            ORDER BY c.nom_cours ASC
        """, (user_id, filiere))
    cours_prof = cursor.fetchall()
    
    # Récupérer la date actuelle pour les présences
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Si un cours est sélectionné, récupérer les présences pour ce cours aujourd'hui
    course_id = request.args.get('course_id', type=int)
    date_cours = request.args.get('date', today)
    
    presences_existantes = {}
    if course_id:
        cursor.execute("""
            SELECT etudiant_id, statut, commentaire
            FROM presences
            WHERE course_id = %s AND date_cours = %s
        """, (course_id, date_cours))
        presences_existantes = {p['etudiant_id']: p for p in cursor.fetchall()}

        conn.close()

    return render_template("prof_class_details.html", 
                               etudiants=etudiants,
                         filiere=filiere,
                         niveau=niveau,
                         nom_classe=nom_classe,
                         classe_exists=classe_exists,
                         cours_prof=cours_prof,
                         course_id=course_id,
                         date_cours=date_cours,
                               presences_existantes=presences_existantes,
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''))

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
    
    if niveau_exists:
        # Correspondance flexible du niveau
        niveau_abbrev = niveau.split()[0] if niveau else ""
        cursor.execute("""
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
            AND c.filiere = %s 
            AND c.niveau IS NOT NULL AND c.niveau != ''
            AND (
                c.niveau = %s 
                OR c.niveau = %s
                OR c.niveau LIKE %s
                OR c.niveau LIKE %s
            )
        """, (course_id, user_id, filiere, niveau, niveau_abbrev, f'{niveau_abbrev}%', f'%{niveau_abbrev}%'))
    else:
        cursor.execute("""
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
            AND c.filiere = %s
        """, (course_id, user_id, filiere))
    
    course = cursor.fetchone()
    if not course:
        conn.close()
        return jsonify({'success': False, 'message': 'Cours non trouvé ou accès refusé pour cette classe'}), 404
    
    # Sauvegarder chaque présence
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
                WHERE id = %s
            """, (statut, commentaire, existing['id']))
        else:
            # Créer une nouvelle présence
            cursor.execute("""
                INSERT INTO presences (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (etudiant_id, course_id, user_id, date_cours, statut, commentaire))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Présences sauvegardées avec succès'})

# 🎯 ROUTE POUR LES ABSENCES ADMIN
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
        
        # Récupérer toutes les absences avec les détails des étudiants et cours
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
        
        # Récupérer uniquement les absences de cet étudiant
        cursor.execute("""
            SELECT p.*, c.nom_cours, c.salle, c.heure_debut, c.heure_fin,
                   prof.nom as prof_nom, prof.prenom as prof_prenom
            FROM presences p
            JOIN courses c ON p.course_id = c.id
            JOIN users prof ON p.professeur_id = prof.id
            WHERE p.etudiant_id = %s AND p.statut = 'absent'
            ORDER BY p.date_cours DESC, p.created_at DESC
        """, (user_id,))
        
        absences = cursor.fetchall()

        # Calculer les statistiques personnelles (absences uniquement)
        cursor.execute("""
            SELECT 
                COUNT(*) as total_absences,
                COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN 1 END) as absences_30j,
                COUNT(CASE WHEN date_cours >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 1 END) as absences_7j
            FROM presences 
            WHERE etudiant_id = %s AND statut = 'absent'
        """, (user_id,))
        
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
                INSERT INTO documents (course_id, nom_cours, filiere, professeur_id, titre, description, type_doc, nom_fichier, chemin_fichier, taille_fichier, date_upload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (course_id, nom_cours, filiere_cours, session['user_id'], titre, description, type_doc, file.filename, filename, len(file_content)))
        except Exception as e:
            # Si erreur (colonnes n'existent pas), utiliser l'ancienne structure
            print(f"Erreur insertion avec nom_cours/filiere, utilisation ancienne structure: {e}")
            cursor.execute("""
                INSERT INTO documents (course_id, professeur_id, titre, description, type_doc, nom_fichier, chemin_fichier, taille_fichier, date_upload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (course_id, session['user_id'], titre, description, type_doc, file.filename, filename, len(file_content)))
        
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
        
        # Récupérer les informations du cours
        cursor.execute("""
            SELECT c.*, u.prenom as prof_prenom, u.nom as prof_nom
            FROM courses c
            JOIN users u ON c.professeur_id = u.id
            WHERE c.id = %s
        """, (course_id,))
        
        course = cursor.fetchone()
        
        if not course:
            flash("Cours non trouvé.", "danger")
            conn.close()
            return redirect(url_for('student_dashboard'))

        # Récupérer les documents du cours (par nom_cours et filiere, disponibles pendant 5 ans)
        nom_cours = course['nom_cours']
        filiere_cours = course['filiere']
        
        # Vérifier si les colonnes nom_cours et filiere existent dans documents
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'nom_cours'")
        nom_cours_exists = cursor.fetchone() is not None
        
        if nom_cours_exists:
            # Récupérer tous les documents de ce module (nom_cours + filiere) de moins de 5 ans
            cursor.execute("""
                SELECT d.*, u.prenom as prof_prenom, u.nom as prof_nom
                FROM documents d
                JOIN users u ON d.professeur_id = u.id
                WHERE d.nom_cours = %s AND d.filiere = %s
                AND d.date_upload >= DATE_SUB(NOW(), INTERVAL 5 YEAR)
                ORDER BY d.date_upload DESC
            """, (nom_cours, filiere_cours))
        else:
            # Si les colonnes n'existent pas encore, utiliser course_id (compatibilité)
            cursor.execute("""
                SELECT d.*, u.prenom as prof_prenom, u.nom as prof_nom
                FROM documents d
                JOIN users u ON d.professeur_id = u.id
                WHERE d.course_id = %s
                ORDER BY d.date_upload DESC
            """, (course_id,))
        
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
            WHERE d.id = %s
        """, (document_id,))
        
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

if __name__ == '__main__':
    app.run(debug=True)