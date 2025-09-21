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
            return redirect(url_for('prof_dashboard'))  # <-- utiliser le dashboard unifié
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
                return redirect(url_for('prof_dashboard'))  # dashboard unifié
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash("Identifiants incorrects.", "danger")
            return render_template('login.html', email=email)

    return render_template('login.html')
@app.route('/professeur/dashboard')
@login_required
def prof_dashboard():
    if session.get('role') != 'professeur':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('login'))

    # 🎯 RÉCUPÉRER UNIQUEMENT LES COURS DE CE PROFESSEUR depuis emploi_temps
    cursor = conn.cursor(dictionary=True)
    courses_query = """
        SELECT c.*, et.visible, et.notifications
        FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'professeur' AND et.visible = 1
        ORDER BY c.jour_semaine, c.heure_debut
    """
    cursor.execute(courses_query, (user_id,))
    cours = cursor.fetchall()

    # 🎯 RÉCUPÉRER LES ÉTUDIANTS UNIQUEMENT POUR SES COURS
    cours_etudiants = {}
    events = []
    filieres_enseignees = set()

    for cours_item in cours:
        # cours_item est déjà un dict avec cursor(dictionary=True)
        cours_dict = cours_item

        # Ajouter la filière à la liste des filières enseignées par ce prof
        if cours_dict['filiere']:
            filieres_enseignees.add(cours_dict['filiere'])

        # Récupérer UNIQUEMENT les étudiants de cette filière pour ce cours
        cursor.execute(
            "SELECT * FROM users WHERE role='etudiant' AND filiere = %s ORDER BY nom ASC",
            (cours_dict['filiere'],)
        )
        etudiants = cursor.fetchall()
        cours_etudiants[cours_dict['id']] = etudiants

        # Préparer les événements pour le calendrier de CE professeur
        title = cours_dict['nom_cours']
        if cours_dict.get('salle'):
            title += f" ({cours_dict['salle']})"
        title += f" - {len(etudiants)} étudiant(s)"

        events.append({
            "title": title,
            "start": cours_dict["start"],
            "end": cours_dict["end"],
            "description": cours_dict.get('description', ''),
            "salle": cours_dict.get('salle', ''),
            "nb_etudiants": len(etudiants),
            "id": cours_dict['id'],
            "filiere": cours_dict['filiere'],
            "jour_semaine": cours_dict.get('jour_semaine', ''),
            "heure_debut": cours_dict.get('heure_debut', ''),
            "heure_fin": cours_dict.get('heure_fin', '')
        })

    # 🎯 CALCULER LES STATISTIQUES UNIQUEMENT POUR CE PROFESSEUR
    # Compter les étudiants uniques (éviter les doublons entre cours)
    total_etudiants_uniques = set()
    for etudiants_list in cours_etudiants.values():
        for etudiant in etudiants_list:
            total_etudiants_uniques.add(etudiant['id'])

    # Calculer les cours d'aujourd'hui pour CE professeur
    from datetime import datetime
    jour_actuel = datetime.now().strftime('%A')
    jours_fr = {
        'Monday': 'Lundi',
        'Tuesday': 'Mardi',
        'Wednesday': 'Mercredi',
        'Thursday': 'Jeudi',
        'Friday': 'Vendredi',
        'Saturday': 'Samedi',
        'Sunday': 'Dimanche'
    }
    jour_fr = jours_fr.get(jour_actuel, 'Lundi')

    cours_aujourd_hui = len([c for c in cours if dict(c).get('jour_semaine') == jour_fr])

    # 🎯 STATISTIQUES PERSONNALISÉES POUR CE PROFESSEUR UNIQUEMENT
    stats = {
        'total_cours': len(cours),
        'total_etudiants': len(total_etudiants_uniques),
        'cours_aujourd_hui': cours_aujourd_hui,
        'filieres_enseignees': len(filieres_enseignees),
        'jour_actuel': jour_fr
    }

    conn.close()

    # Organiser les cours par jour pour le nouveau template
    jours_semaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    emploi_temps = {}

    for jour in jours_semaine:
        emploi_temps[jour] = []

    for course in cours:
        course_dict = dict(course)
        if course_dict['jour_semaine'] in emploi_temps:
            emploi_temps[course_dict['jour_semaine']].append(course_dict)

    # Organiser les étudiants par filière et niveau
    filieres_etudiants = {}
    all_etudiants = []

    for cours_id, etudiants_list in cours_etudiants.items():
        for etudiant in etudiants_list:
            etudiant_dict = dict(etudiant)
            if etudiant_dict not in all_etudiants:
                all_etudiants.append(etudiant_dict)

    for etudiant in all_etudiants:
        filiere = etudiant['filiere']
        niveau = etudiant['niveau']

        if filiere not in filieres_etudiants:
            filieres_etudiants[filiere] = {}

        if niveau not in filieres_etudiants[filiere]:
            filieres_etudiants[filiere][niveau] = []

        if etudiant not in filieres_etudiants[filiere][niveau]:
            filieres_etudiants[filiere][niveau].append(etudiant)

    return render_template('prof_dashboard_ultra.html',
                           cours=cours,
                           cours_etudiants=cours_etudiants,
                           events=events,
                           stats=stats,
                           emploi_temps=emploi_temps,
                           filieres_etudiants=filieres_etudiants,
                           nom=session.get('nom', ''),
                           prenom=session.get('prenom', ''))

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
    courses_query = """
        SELECT c.* FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant' AND c.filiere = %s AND c.niveau = %s
        ORDER BY c.start
    """
    cursor.execute(courses_query, (user_id, user_filiere, user_niveau))
    raw_courses = cursor.fetchall()
    conn.close()

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
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('login'))

    cursor = conn.cursor(dictionary=True)
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

    # 1. Cours cette semaine
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())  # Lundi de cette semaine
    end_of_week = start_of_week + timedelta(days=6)  # Dimanche de cette semaine

    cursor.execute('''
        SELECT COUNT(*) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND date(c.date_cours) BETWEEN %s AND %s
    ''', (user_id, start_of_week.strftime('%Y-%m-%d'), end_of_week.strftime('%Y-%m-%d')))
    cours_cette_semaine = cursor.fetchone()

    # Cours semaine dernière pour comparaison
    start_last_week = start_of_week - timedelta(days=7)
    end_last_week = start_of_week - timedelta(days=1)

    cursor.execute('''
        SELECT COUNT(*) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND date(c.date_cours) BETWEEN %s AND %s
    ''', (user_id, start_last_week.strftime('%Y-%m-%d'), end_last_week.strftime('%Y-%m-%d')))
    cours_semaine_derniere = cursor.fetchone()

    # 2. Absences à justifier
    cursor.execute('''
        SELECT COUNT(*) as count FROM presences p
        JOIN courses c ON p.course_id = c.id
        WHERE p.etudiant_id = %s AND p.statut IN ('absent', 'retard')
        AND (p.commentaire IS NULL OR p.commentaire = '')
    ''', (user_id,))
    absences_a_justifier = cursor.fetchone()

    # 3. Prochains examens (cours avec "examen" dans le nom ou description)
    cursor.execute('''
        SELECT COUNT(*) as count FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND (LOWER(c.nom_cours) LIKE '%%examen%%' OR LOWER(c.description) LIKE '%%examen%%'
             OR LOWER(c.nom_cours) LIKE '%%test%%' OR LOWER(c.description) LIKE '%%test%%')
        AND date(c.date_cours) > CURDATE()
    ''', (user_id,))
    prochains_examens = cursor.fetchone()

    # Prochain examen le plus proche
    cursor.execute('''
        SELECT MIN(date(c.date_cours)) as next_exam FROM courses c
        JOIN emploi_temps et ON c.id = et.course_id
        WHERE et.user_id = %s AND et.role = 'etudiant'
        AND (LOWER(c.nom_cours) LIKE '%%examen%%' OR LOWER(c.description) LIKE '%%examen%%'
             OR LOWER(c.nom_cours) LIKE '%%test%%' OR LOWER(c.description) LIKE '%%test%%')
        AND date(c.date_cours) > CURDATE()
    ''', (user_id,))
    prochain_examen_date = cursor.fetchone()

    # Calculer les jours jusqu'au prochain examen
    jours_prochain_examen = 0
    if prochain_examen_date and prochain_examen_date['next_exam']:
        exam_date = datetime.strptime(prochain_examen_date['next_exam'], '%Y-%m-%d')
        jours_prochain_examen = (exam_date - today).days

    # 4. Moyenne générale (simulée pour l'instant - vous pouvez ajouter une table notes)
    # Pour l'instant, on génère une moyenne basée sur la présence
    total_cours = len(raw_courses)
    total_absences = absences_a_justifier['count'] if absences_a_justifier else 0

    # Calcul simple : 20 - (absences * 2), minimum 10
    moyenne_generale = max(10.0, 20.0 - (total_absences * 1.5)) if total_cours > 0 else 15.0

    # Statistiques pour le template
    stats = {
        'cours_cette_semaine': cours_cette_semaine['count'] if cours_cette_semaine else 0,
        'cours_semaine_derniere': cours_semaine_derniere['count'] if cours_semaine_derniere else 0,
        'moyenne_generale': round(moyenne_generale, 1),
        'absences_a_justifier': total_absences,
        'prochains_examens': prochains_examens['count'] if prochains_examens else 0,
        'jours_prochain_examen': jours_prochain_examen
    }

    # Calcul des variations
    stats['variation_cours'] = stats['cours_cette_semaine'] - stats['cours_semaine_derniere']
    stats['variation_moyenne'] = 0.3  # Simulé pour l'instant

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
    cursor.execute("SELECT id, prenom, nom FROM users WHERE role = 'etudiant' ORDER BY nom")
    etudiants = cursor.fetchall()

    cursor.execute("""
        SELECT p.date, u.prenom, u.nom, p.observation AS description, p.montant, 'Recette' AS type
        FROM paiements p
        JOIN users u ON p.etudiant_id = u.id
        ORDER BY p.date DESC
    """)
    transactions = cursor.fetchall()

    conn.close()

    return render_template('etudiants_paiements.html', etudiants=etudiants, transactions=transactions)

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
    groupes = {}
    for etu in etudiants:
        key = (etu['filiere'], etu['niveau'])
        if key not in groupes:
            groupes[key] = []
        groupes[key].append(etu)

    conn.close()
    return render_template("admin_filieres.html", groupes=groupes, classe_exists=classe_exists)


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

    # Récupérer la filière de l'étudiant
    cursor.execute("SELECT filiere FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return []

    filiere = row["filiere"]

    # Récupérer les cours liés à cette filière
    cursor.execute("SELECT nom_cours AS title, start, end FROM courses WHERE filiere = %s", (filiere,))
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
    user_id = session['user_id']
    user_filiere = session.get('filiere', 'IAM')  # Par défaut IAM

    events = get_student_events(user_id)
    cours_set = set(event['title'] for event in events)
    cours = sorted(cours_set)

    notes = get_student_notes(user_id, cours)

    return render_template('student_courses.html', cours=cours, notes=notes, filiere=user_filiere)


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

    # Récupérer toutes les notes associées aux étudiants
    cursor.execute('''
        SELECT id, etudiant_id, nom_cours, CC1, CC2, Participation, Examen
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
        notes_par_etudiant=notes_par_etudiant
    )

@app.route('/admin/bulletin/<int:etudiant_id>')
def admin_bulletin(etudiant_id):
    """Générer le bulletin d'un étudiant"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('login'))
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
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
        
        # Récupérer les notes de l'étudiant
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
        etablissement = {
            'nom': 'ADS CLASS',
            'adresse': 'Niamey, Niger',
            'telephone': '+227 XX XX XX XX',
            'email': 'contact@adsclass.ne',
            'site_web': 'www.adsclass.ne',
            'directeur': 'Dr. Directeur',
            'annee_scolaire': '2024-2025',
            'semestre': 'Semestre 1'
        }
        
        conn.close()
        
        generation_date = datetime.now().strftime('%d/%m/%Y à %H:%M')
        return render_template('admin_bulletin.html', 
                             etudiant=etudiant, 
                             notes=notes, 
                             stats=stats,
                             etablissement=etablissement,
                             generation_date=generation_date)
        
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


@app.route('/admin/saisir_notes/<int:etudiant_id>', methods=['GET', 'POST'])
def saisir_notes(etudiant_id):
    conn = get_db_connection()
    if not conn:
        flash("Erreur de connexion à la base de données.", "danger")
        return redirect(url_for('admin_grades'))
    
    cursor = conn.cursor(dictionary=True)
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

        # Insertion simple (ou update selon ta logique)
        cursor.execute('''
            INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (etudiant_id, nom_cours, cc1, cc2, participation, examen))
        conn.commit()
        conn.close()

        flash("Notes enregistrées avec succès.", "success")
        return redirect(url_for('admin_grades'))

    conn.close()
    return render_template('saisir_notes.html', etudiant=etu)

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

    # Récupération des cours liés à cet étudiant
    cursor.execute("SELECT DISTINCT nom_cours FROM notes WHERE etudiant_id = %s", (user_id,))
    cours = [row['nom_cours'] for row in cursor.fetchall()]

    notes_dict = {}
    for nom_cours in cours:
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
def test_prof_dashboard():
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

# Route pour voir les étudiants d'un cours (professeur)
@app.route('/professeur/cours/<int:course_id>/etudiants')
@login_required
def professeur_etudiants_cours(course_id):
    if session.get('role') != 'professeur':
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données', 'error')
        return redirect(url_for('prof_dashboard'))
    
    cursor = conn.cursor(dictionary=True)

    try:
        # Vérifier que le cours appartient au professeur connecté
        cursor.execute('''
        SELECT * FROM courses
        WHERE id = %s AND professeur_id = %s
        ''', (course_id, session['user_id']))

        course = cursor.fetchone()
        if not course:
            flash('Cours non trouvé ou accès non autorisé', 'error')
            return redirect(url_for('prof_dashboard'))

        # Récupérer les étudiants inscrits à ce cours
        cursor.execute('''
        SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau
        FROM users u
        JOIN emploi_temps et ON u.id = et.user_id
        WHERE et.course_id = %s AND et.role = 'etudiant' AND u.role = 'etudiant'
        ORDER BY u.nom, u.prenom
        ''', (course_id,))

        etudiants = cursor.fetchall()

        # Récupérer les présences d'aujourd'hui pour ce cours
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
        SELECT etudiant_id, statut, commentaire
        FROM presences
        WHERE course_id = %s AND date_cours = %s
        ''', (course_id, today))

        presences_today = {row['etudiant_id']: {'statut': row['statut'], 'commentaire': row['commentaire']}
                          for row in cursor.fetchall()}

        conn.close()

        return render_template('professeur_etudiants_cours.html',
                             course=course,
                             etudiants=etudiants,
                             presences_today=presences_today,
                             today=today,
                             nom=session.get('nom'),
                             prenom=session.get('prenom'))

    except Exception as e:
        conn.close()
        flash(f'Erreur lors du chargement des étudiants: {str(e)}', 'error')
        return redirect(url_for('prof_dashboard'))

# Route pour enregistrer les présences
@app.route('/professeur/cours/<int:course_id>/presences', methods=['POST'])
@login_required
def enregistrer_presences(course_id):
    if session.get('role') != 'professeur':
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('Erreur de connexion à la base de données', 'error')
        return redirect(url_for('prof_dashboard'))
    
    cursor = conn.cursor()

    try:
        date_cours = request.form.get('date_cours')
        if not date_cours:
            date_cours = datetime.now().strftime('%Y-%m-%d')

        # Récupérer tous les étudiants du cours
        cursor.execute('''
        SELECT u.id
        FROM users u
        JOIN emploi_temps et ON u.id = et.user_id
        WHERE et.course_id = %s AND et.role = 'etudiant'
        ''', (course_id,))

        etudiants = cursor.fetchall()

        # Enregistrer les présences
        for etudiant in etudiants:
            etudiant_id = etudiant['id']
            statut = request.form.get(f'presence_{etudiant_id}', 'absent')
            commentaire = request.form.get(f'commentaire_{etudiant_id}', '')

            # Insérer ou mettre à jour la présence (MySQL utilise ON DUPLICATE KEY UPDATE)
            cursor.execute('''
            INSERT INTO presences
            (etudiant_id, course_id, professeur_id, date_cours, statut, commentaire, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            statut = VALUES(statut),
            commentaire = VALUES(commentaire),
            updated_at = VALUES(updated_at)
            ''', (etudiant_id, course_id, session['user_id'], date_cours, statut, commentaire,
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()
        conn.close()

        flash('Présences enregistrées avec succès!', 'success')
        return redirect(url_for('professeur_etudiants_cours', course_id=course_id))

    except Exception as e:
        conn.close()
        flash(f'Erreur lors de l\'enregistrement des présences: {str(e)}', 'error')
        return redirect(url_for('professeur_etudiants_cours', course_id=course_id))

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
            return redirect(url_for('prof_dashboard'))
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM courses WHERE id = %s AND professeur_id = %s',
                      (course_id, session['user_id']))
        course = cursor.fetchone()
        conn.close()

        if not course:
            flash('Cours non trouvé', 'error')
            return redirect(url_for('prof_dashboard'))

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
            
            cursor = conn.cursor()
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
            return redirect(url_for('professeur_etudiants_cours', course_id=course_id))

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
                return redirect(url_for('prof_dashboard'))

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

# 🎯 GESTION DES PRÉSENCES PAR PROFESSEUR
@app.route('/professeur/presences/<int:course_id>')
@login_required
def professeur_presences(course_id):
    """Page de gestion des présences pour un cours spécifique"""
    try:
        if session.get('role') != 'professeur':
            flash("Accès refusé.", "danger")
            return redirect(url_for('login'))

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('prof_dashboard'))

        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que le professeur a accès à ce cours
        cursor.execute("""
            SELECT c.*, et.visible, et.notifications
            FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
        """, (course_id, user_id))
        
        course = cursor.fetchone()
        if not course:
            flash("Cours non trouvé ou accès refusé.", "danger")
            conn.close()
            return redirect(url_for('prof_dashboard'))

        # Récupérer tous les étudiants de la filière ET du niveau de ce cours
        cursor.execute("""
            SELECT u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau
            FROM users u
            WHERE u.role = 'etudiant' AND u.filiere = %s AND u.niveau = %s
            ORDER BY u.nom, u.prenom
        """, (course['filiere'], course['niveau']))
        
        etudiants = cursor.fetchall()

        # Récupérer les présences existantes pour ce cours aujourd'hui
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT etudiant_id, statut, commentaire, created_at
            FROM presences
            WHERE course_id = %s AND date_cours = %s
        """, (course_id, today))
        
        presences_existantes = {p['etudiant_id']: p for p in cursor.fetchall()}

        # Récupérer l'historique des présences pour ce cours (7 derniers jours)
        cursor.execute("""
            SELECT p.*, u.nom, u.prenom
            FROM presences p
            JOIN users u ON p.etudiant_id = u.id
            WHERE p.course_id = %s AND p.date_cours >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            ORDER BY p.date_cours DESC, u.nom, u.prenom
        """, (course_id,))
        
        historique = cursor.fetchall()

        conn.close()

        return render_template('professeur_presences.html',
                               course=course,
                               etudiants=etudiants,
                               presences_existantes=presences_existantes,
                               historique=historique,
                               today=today,
                               nom=session.get('nom', ''),
                               prenom=session.get('prenom', ''))

    except Exception as e:
        import traceback
        error_msg = f"Erreur gestion présences: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement des présences.", "danger")
        return redirect(url_for('prof_dashboard'))

@app.route('/professeur/presences/<int:course_id>/save', methods=['POST'])
@login_required
def save_presences(course_id):
    """Sauvegarder les présences pour un cours"""
    try:
        if session.get('role') != 'professeur':
            return jsonify({'success': False, 'message': 'Accès refusé'}), 403

        user_id = session['user_id']
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        
        # Vérifier que le professeur a accès à ce cours
        cursor.execute("""
            SELECT c.* FROM courses c
            JOIN emploi_temps et ON c.id = et.course_id
            WHERE c.id = %s AND et.user_id = %s AND et.role = 'professeur'
        """, (course_id, user_id))
        
        course = cursor.fetchone()
        if not course:
            conn.close()
            return jsonify({'success': False, 'message': 'Cours non trouvé'}), 404

        # Récupérer les données du formulaire
        presences_data = request.get_json()
        date_cours = presences_data.get('date_cours', datetime.now().strftime('%Y-%m-%d'))
        
        # Sauvegarder chaque présence
        for etudiant_id, presence_info in presences_data.get('presences', {}).items():
            statut = presence_info.get('statut')  # 'present', 'absent', 'retard'
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

    except Exception as e:
        import traceback
        error_msg = f"Erreur sauvegarde présences: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return jsonify({'success': False, 'message': 'Erreur lors de la sauvegarde'}), 500

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
            return redirect(url_for('prof_dashboard'))

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
            return redirect(url_for('prof_dashboard'))

        conn.close()
        
        return render_template('professeur_upload_document.html', 
                               course=course, 
                               prenom=course['prenom'])

    except Exception as e:
        import traceback
        error_msg = f"Erreur upload document: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        flash("Erreur lors du chargement de la page d'upload.", "error")
        return redirect(url_for('prof_dashboard'))

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

        cursor = conn.cursor()
        
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

        # Récupérer les documents du cours
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