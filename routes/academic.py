"""
Module Académique — AdsClass
Routes d'administration des filières et modules (CRUD + API).
"""

import tenant
import mysql.connector
from permissions import require_permission


def register_academic_routes(app, deps):
    """Enregistrer les routes filières/modules sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, flash, jsonify

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']

    # ============================================================
    # 🎓 ROUTES POUR GESTION DES FILIÈRES ET MODULES
    # ============================================================

    @app.route('/admin/filieres-modules')
    @login_required
    @admin_required
    @require_permission('classes.manage')
    def admin_filieres_modules():
        """Page de gestion des filières et modules"""
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_home'))

        cursor = conn.cursor(dictionary=True)

        # Récupérer toutes les filières avec le nombre de modules
        cursor.execute("""
            SELECT f.*,
                   (SELECT COUNT(*) FROM modules WHERE filiere_id = f.id) as nb_modules,
                   (SELECT COUNT(*) FROM modules WHERE filiere_id = f.id AND est_actif = TRUE) as nb_modules_actifs
            FROM filieres f
            WHERE f.school_id = %s
            ORDER BY f.nom
        """, (tenant.current_school_id(),))
        filieres = cursor.fetchall()

        # Récupérer tous les modules groupés par filière
        cursor.execute("""
            SELECT m.*, f.nom as filiere_nom, f.code as filiere_code
            FROM modules m
            JOIN filieres f ON m.filiere_id = f.id
            WHERE m.school_id = %s
            ORDER BY f.nom, m.semestre, m.nom
        """, (tenant.current_school_id(),))
        modules = cursor.fetchall()

        cursor.close()
        conn.close()

        return render_template('admin_filieres_modules.html',
                             filieres=filieres,
                             modules=modules)

    @app.route('/admin/filieres/create', methods=['POST'])
    @login_required
    @admin_required
    def create_filiere():
        """Créer une nouvelle filière"""
        nom = request.form.get('nom', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        niveau = request.form.get('niveau', '').strip()
        duree_annees = request.form.get('duree_annees', '1')

        if not nom:
            flash('Le nom de la filière est obligatoire.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO filieres (nom, code, description, niveau, duree_annees, school_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (nom, code if code else None, description, niveau, int(duree_annees), tenant.current_school_id()))
            conn.commit()
            flash(f'Filière "{nom}" créée avec succès!', 'success')
        except mysql.connector.IntegrityError:
            flash(f'Une filière avec ce nom ou code existe déjà.', 'danger')
        except Exception as e:
            flash(f'Erreur lors de la création: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/filieres/<int:filiere_id>/edit', methods=['POST'])
    @login_required
    @admin_required
    def edit_filiere(filiere_id):
        """Modifier une filière"""
        nom = request.form.get('nom', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        niveau = request.form.get('niveau', '').strip()
        duree_annees = request.form.get('duree_annees', '1')

        if not nom:
            flash('Le nom de la filière est obligatoire.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE filieres
                SET nom = %s, code = %s, description = %s, niveau = %s, duree_annees = %s
                WHERE id = %s AND school_id = %s
            """, (nom, code if code else None, description, niveau, int(duree_annees), filiere_id, tenant.current_school_id()))
            conn.commit()
            flash(f'Filière "{nom}" modifiée avec succès!', 'success')
        except mysql.connector.IntegrityError:
            flash(f'Une filière avec ce nom ou code existe déjà.', 'danger')
        except Exception as e:
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/filieres/<int:filiere_id>/delete', methods=['POST'])
    @login_required
    @admin_required
    def delete_filiere(filiere_id):
        """Supprimer une filière"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor(dictionary=True)

            # Vérifier si la filière existe
            cursor.execute("SELECT nom FROM filieres WHERE id = %s", (filiere_id,))
            filiere = cursor.fetchone()

            if not filiere:
                flash('Filière non trouvée.', 'danger')
                return redirect(url_for('admin_filieres_modules'))

            # Supprimer la filière (les modules seront supprimés en cascade)
            cursor.execute("DELETE FROM filieres WHERE id = %s AND school_id = %s", (filiere_id, tenant.current_school_id()))
            conn.commit()
            flash(f'Filière "{filiere["nom"]}" supprimée avec succès!', 'success')
        except Exception as e:
            flash(f'Erreur lors de la suppression: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/filieres/<int:filiere_id>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def toggle_filiere(filiere_id):
        """Activer/Désactiver une filière"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT nom, est_active FROM filieres WHERE id = %s", (filiere_id,))
            filiere = cursor.fetchone()

            if filiere:
                new_status = not filiere['est_active']
                cursor.execute("UPDATE filieres SET est_active = %s WHERE id = %s AND school_id = %s", (new_status, filiere_id, tenant.current_school_id()))
                conn.commit()
                status_text = "activée" if new_status else "désactivée"
                flash(f'Filière "{filiere["nom"]}" {status_text}!', 'success')
        except Exception as e:
            flash(f'Erreur: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/modules/create', methods=['POST'])
    @login_required
    @admin_required
    def create_module():
        """Créer un nouveau module"""
        filiere_id = request.form.get('filiere_id')
        nom = request.form.get('nom', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        credits = request.form.get('credits', '0')
        coefficient = request.form.get('coefficient', '1.0')
        semestre = request.form.get('semestre', '1')
        niveau = request.form.get('niveau', '').strip()
        est_obligatoire = request.form.get('est_obligatoire') == 'on'

        if not filiere_id or not nom:
            flash('La filière et le nom du module sont obligatoires.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO modules (filiere_id, nom, code, description, credits, coefficient, semestre, niveau, est_obligatoire, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (int(filiere_id), nom, code if code else None, description,
                  int(credits), float(coefficient), int(semestre), niveau, est_obligatoire, tenant.current_school_id()))
            conn.commit()
            flash(f'Module "{nom}" créé avec succès!', 'success')
        except mysql.connector.IntegrityError:
            flash(f'Un module avec ce nom existe déjà pour cette filière et ce semestre.', 'danger')
        except Exception as e:
            flash(f'Erreur lors de la création: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/modules/<int:module_id>/edit', methods=['POST'])
    @login_required
    @admin_required
    def edit_module(module_id):
        """Modifier un module"""
        nom = request.form.get('nom', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        credits = request.form.get('credits', '0')
        coefficient = request.form.get('coefficient', '1.0')
        semestre = request.form.get('semestre', '1')
        niveau = request.form.get('niveau', '').strip()
        est_obligatoire = request.form.get('est_obligatoire') == 'on'

        if not nom:
            flash('Le nom du module est obligatoire.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE modules
                SET nom = %s, code = %s, description = %s, credits = %s,
                    coefficient = %s, semestre = %s, niveau = %s, est_obligatoire = %s
                WHERE id = %s AND school_id = %s
            """, (nom, code if code else None, description, int(credits),
                  float(coefficient), int(semestre), niveau, est_obligatoire, module_id, tenant.current_school_id()))
            conn.commit()
            flash(f'Module "{nom}" modifié avec succès!', 'success')
        except mysql.connector.IntegrityError:
            flash(f'Un module avec ce nom existe déjà pour cette filière et ce semestre.', 'danger')
        except Exception as e:
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/modules/<int:module_id>/delete', methods=['POST'])
    @login_required
    @admin_required
    def delete_module(module_id):
        """Supprimer un module"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor(dictionary=True)

            # Vérifier si le module existe
            cursor.execute("SELECT nom FROM modules WHERE id = %s", (module_id,))
            module = cursor.fetchone()

            if not module:
                flash('Module non trouvé.', 'danger')
                return redirect(url_for('admin_filieres_modules'))

            # Supprimer le module
            cursor.execute("DELETE FROM modules WHERE id = %s AND school_id = %s", (module_id, tenant.current_school_id()))
            conn.commit()
            flash(f'Module "{module["nom"]}" supprimé avec succès!', 'success')
        except Exception as e:
            flash(f'Erreur lors de la suppression: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/admin/modules/<int:module_id>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def toggle_module(module_id):
        """Activer/Désactiver un module"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion.', 'danger')
            return redirect(url_for('admin_filieres_modules'))

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT nom, est_actif FROM modules WHERE id = %s", (module_id,))
            module = cursor.fetchone()

            if module:
                new_status = not module['est_actif']
                cursor.execute("UPDATE modules SET est_actif = %s WHERE id = %s AND school_id = %s", (new_status, module_id, tenant.current_school_id()))
                conn.commit()
                status_text = "activé" if new_status else "désactivé"
                flash(f'Module "{module["nom"]}" {status_text}!', 'success')
        except Exception as e:
            flash(f'Erreur: {str(e)}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_filieres_modules'))

    @app.route('/api/filieres/<int:filiere_id>/modules')
    @login_required
    @admin_required
    def get_filiere_modules(filiere_id):
        """API pour récupérer les modules d'une filière"""
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM modules
            WHERE filiere_id = %s AND school_id = %s
            ORDER BY semestre, nom
        """, (filiere_id, tenant.current_school_id()))
        modules = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({'success': True, 'modules': modules})
