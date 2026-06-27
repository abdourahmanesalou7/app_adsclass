"""
Module Étudiants — AdsClass
Routes de gestion des paiements de scolarité et des reçus (vue admin).
"""

import io
import csv
import tenant


def register_students_routes(app, deps):
    """Enregistrer les routes Étudiants (paiements / reçus) sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, session, flash, jsonify, send_file

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']
    get_current_year_id = deps['get_current_year_id']

    # ============================================================
    # 💳 PAIEMENTS DE SCOLARITÉ
    # ============================================================

    @app.route('/admin/etudiants/paiements')
    @login_required
    @admin_required
    def etudiants_paiements():
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_home'))

        year_id = get_current_year_id()
        cursor = conn.cursor(dictionary=True)

        # Liste enrichie des étudiants avec filière, niveau et stats de paiement (filtré par année)
        if year_id:
            cursor.execute("""
                SELECT
                    u.id, u.prenom, u.nom, u.email,
                    u.filiere, u.niveau,
                    COALESCE(SUM(CASE WHEN p.annee_academique_id = %s THEN p.montant ELSE 0 END), 0) AS total_paye,
                    COUNT(CASE WHEN p.annee_academique_id = %s THEN p.id END) AS nombre_paiements,
                    MAX(CASE WHEN p.annee_academique_id = %s THEN p.date END) AS dernier_paiement
                FROM users u
                LEFT JOIN paiements p ON p.etudiant_id = u.id
                WHERE u.role = 'etudiant' AND u.school_id = %s
                GROUP BY u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau
                ORDER BY u.filiere ASC, u.niveau ASC, u.nom ASC, u.prenom ASC
            """, (year_id, year_id, year_id, tenant.current_school_id()))
        else:
            cursor.execute("""
                SELECT
                    u.id, u.prenom, u.nom, u.email,
                    u.filiere, u.niveau,
                    COALESCE(SUM(p.montant), 0) AS total_paye,
                    COUNT(p.id) AS nombre_paiements,
                    MAX(p.date) AS dernier_paiement
                FROM users u
                LEFT JOIN paiements p ON p.etudiant_id = u.id
                WHERE u.role = 'etudiant' AND u.school_id = %s
                GROUP BY u.id, u.prenom, u.nom, u.email, u.filiere, u.niveau
                ORDER BY u.filiere ASC, u.niveau ASC, u.nom ASC, u.prenom ASC
            """, (tenant.current_school_id(),))
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

        if year_id:
            cursor.execute("""
                SELECT p.date, u.prenom, u.nom, p.observation AS description, p.montant, 'Recette' AS type
                FROM paiements p
                JOIN users u ON p.etudiant_id = u.id
                WHERE p.annee_academique_id = %s AND p.school_id = %s
                ORDER BY p.date DESC
            """, (year_id, tenant.current_school_id()))
        else:
            cursor.execute("""
                SELECT p.date, u.prenom, u.nom, p.observation AS description, p.montant, 'Recette' AS type
                FROM paiements p
                JOIN users u ON p.etudiant_id = u.id
                WHERE p.school_id = %s
                ORDER BY p.date DESC
            """, (tenant.current_school_id(),))
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

            year_id = get_current_year_id()
            cursor.execute(
                "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation, annee_academique_id, school_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (etudiant_id, date, montant, moyen, observation, year_id, tenant.current_school_id())
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
        cursor.execute("SELECT prenom, nom FROM users WHERE id = %s AND school_id = %s", (etudiant_id, tenant.current_school_id()))
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
            year_id = get_current_year_id()

            cursor.execute(
                "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation, annee_academique_id, school_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (etudiant_id, date, montant, moyen, observation, year_id, tenant.current_school_id())
            )
            conn.commit()
            flash("Paiement ajouté.", "success")

        year_id = get_current_year_id()
        if year_id:
            cursor.execute(
                "SELECT id, date, montant, moyen, observation FROM paiements WHERE etudiant_id = %s AND annee_academique_id = %s ORDER BY date DESC",
                (etudiant_id, year_id)
            )
        else:
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
        cursor.execute("SELECT * FROM users WHERE id = %s AND school_id = %s", (etudiant_id, tenant.current_school_id()))
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

            year_id = get_current_year_id()
            cursor.execute(
                "INSERT INTO paiements (etudiant_id, date, montant, moyen, observation, annee_academique_id, school_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (etudiant_id, date, montant_float, moyen, observation, year_id, tenant.current_school_id())
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
            "UPDATE paiements SET date = %s, montant = %s, moyen = %s, observation = %s WHERE id = %s AND etudiant_id = %s AND school_id = %s",
            (date, montant, moyen, observation, paiement_id, etudiant_id, tenant.current_school_id())
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
            "DELETE FROM paiements WHERE id = %s AND etudiant_id = %s AND school_id = %s",
            (paiement_id, etudiant_id, tenant.current_school_id())
        )
        conn.commit()
        deleted_rows = cursor.rowcount
        conn.close()
        if deleted_rows == 0:
            return jsonify({'error': 'Paiement non trouvé'}), 404
        return jsonify({'message': 'Paiement supprimé avec succès'})

    # ============================================================
    # 🧾 REÇUS DE PAIEMENT
    # ============================================================

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
                "SELECT * FROM users WHERE id = %s AND role = 'etudiant' AND school_id = %s",
                (paiement_row['etudiant_id'], tenant.current_school_id())
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
                "SELECT * FROM users WHERE id = %s AND role = 'etudiant' AND school_id = %s",
                (etudiant_id, tenant.current_school_id())
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
