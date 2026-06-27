"""
Module Notes / Examens — AdsClass
Routes d'administration des notes : grille, bulletin, saisie, modification, visibilité.
"""

import tenant
from datetime import datetime
from mysql.connector import Error
from student_enrollment_service import build_classes_par_filiere


def register_grades_routes(app, deps):
    """Enregistrer les routes notes/examens sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, flash, jsonify, session

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']
    get_current_year_id = deps['get_current_year_id']

    @app.route('/admin/grades')
    def admin_grades():
        """Grille des notes — mêmes filières canoniques que Gestion des classes."""
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_home'))

        cursor = conn.cursor(dictionary=True)
        classes_par_filiere, filieres_actives = build_classes_par_filiere(cursor, tenant.current_school_id())
        conn.commit()
        conn.close()

        return render_template(
            'admin_classes_grid.html',
            classes_par_filiere=classes_par_filiere,
            filieres_actives=filieres_actives,
            mode='notes',
            page_title='Gestion des Notes'
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
                WHERE u.id = %s AND u.role = 'etudiant' AND u.school_id = %s
            """, (etudiant_id, tenant.current_school_id()))

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
        cursor.execute('SELECT * FROM notes WHERE id = %s AND school_id = %s', (note_id, tenant.current_school_id()))
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
                WHERE id = %s AND school_id = %s
            ''', (CC1, CC2, Participation, Examen, note_id, tenant.current_school_id()))
            conn.commit()
            conn.close()

            flash("Note mise à jour avec succès.")
            return redirect(url_for('admin_grades'))

        conn.close()
        return render_template('modifier_note.html', note=note)

    @app.route('/admin/notes/<int:note_id>/toggle_visible', methods=['POST'])
    @login_required
    @admin_required
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
            cursor.execute("SELECT visible FROM notes WHERE id = %s AND school_id = %s", (note_id, tenant.current_school_id()))
            note = cursor.fetchone()

            if not note:
                conn.close()
                return jsonify({'success': False, 'message': 'Note non trouvée'}), 404

            # Inverser l'état
            new_visible = 1 if note['visible'] == 0 else 0

            # Mettre à jour
            cursor.execute("UPDATE notes SET visible = %s WHERE id = %s AND school_id = %s",
                           (new_visible, note_id, tenant.current_school_id()))
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

        cursor.execute('SELECT * FROM users WHERE id = %s AND school_id = %s', (etudiant_id, tenant.current_school_id()))
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
                        WHERE id = %s AND school_id = %s
                    ''', (cc1, cc2, participation, examen, existing['id'], tenant.current_school_id()))
                else:
                    # Insertion
                    year_id = get_current_year_id()
                    cursor.execute('''
                        INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, semestre, annee_academique_id, school_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (etudiant_id, nom_cours, cc1, cc2, participation, examen, int(semestre), year_id, tenant.current_school_id()))
            else:
                # Insertion sans semestre (rétrocompatibilité)
                year_id = get_current_year_id()
                cursor.execute('''
                    INSERT INTO notes (etudiant_id, nom_cours, CC1, CC2, Participation, Examen, annee_academique_id, school_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (etudiant_id, nom_cours, cc1, cc2, participation, examen, year_id, tenant.current_school_id()))

            conn.commit()
            conn.close()

            flash("Notes enregistrées avec succès pour le semestre " + semestre + ".", "success")
            return redirect(url_for('admin_grades'))

        conn.close()
        return render_template('saisir_notes.html', etudiant=etu, semestre_exists=semestre_exists)
