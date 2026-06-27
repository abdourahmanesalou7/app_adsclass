"""
Module Administration - AdsClass
Routes d'administration des roles et permissions (CRUD roles, attribution, verification).
Extrait de app.py sans aucune modification de logique.
"""

import tenant
from mysql.connector import Error
from permissions import PermissionManager, require_permission


def register_admin_routes(app, deps):
    """Enregistrer les routes roles/permissions sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, flash, jsonify, session

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']

    @app.route('/admin/roles')
    @login_required
    @admin_required
    @require_permission('users.roles')
    def admin_roles():
        """Page de gestion des rôles administratifs"""
        roles = PermissionManager.get_all_roles()
        permissions = PermissionManager.get_all_permissions()

        # Récupérer le nombre d'utilisateurs par rôle
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT admin_role_id, COUNT(*) as count
                FROM users
                WHERE admin_role_id IS NOT NULL AND school_id = %s
                GROUP BY admin_role_id
            ''', (tenant.current_school_id(),))
            user_counts = {row['admin_role_id']: row['count'] for row in cursor.fetchall()}
            cursor.close()
            conn.close()
        else:
            user_counts = {}

        # Rôles archivés de l'école courante
        archived_roles = PermissionManager.get_archived_roles()

        # Ajouter le nombre d'utilisateurs et les permissions à chaque rôle
        for role in roles:
            role['user_count'] = user_counts.get(role['id'], 0)
            role['permissions'] = PermissionManager.get_role_permissions(role['id'])
        for role in archived_roles:
            role['user_count'] = user_counts.get(role['id'], 0)
            role['permissions'] = PermissionManager.get_role_permissions(role['id'])

        return render_template('admin_roles.html',
                             roles=roles,
                             archived_roles=archived_roles,
                             permissions=permissions,
                             modules_labels={
                                 'cours': 'Cours',
                                 'finance': 'Finances',
                                 'notes': 'Notes',
                                 'presences': 'Présences',
                                 'users': 'Utilisateurs',
                                 'classes': 'Classes',
                                 'stats': 'Statistiques',
                                 'system': 'Système'
                             })


    @app.route('/admin/roles/create', methods=['POST'])
    @login_required
    @admin_required
    @require_permission('users.roles')
    def create_role():
        """Créer un nouveau rôle"""
        nom = request.form.get('nom')
        description = request.form.get('description', '')
        couleur = request.form.get('couleur', '#6366f1')
        icone = request.form.get('icone', 'fa-user-shield')
        priorite = request.form.get('priorite', 0)
        permissions = request.form.getlist('permissions')

        if not nom:
            flash('Le nom du rôle est requis.', 'danger')
            return redirect(url_for('admin_roles'))

        role_id, error = PermissionManager.create_role(
            nom, description, couleur, icone, priorite, permissions)
        if role_id:
            flash(f'Rôle "{nom}" créé avec succès.', 'success')
        else:
            flash(f'Erreur lors de la création du rôle: {error}', 'danger')

        return redirect(url_for('admin_roles'))


    @app.route('/admin/roles/<int:role_id>/edit', methods=['GET', 'POST'])
    @login_required
    @admin_required
    @require_permission('users.roles')
    def edit_role(role_id):
        """Modifier un rôle"""
        if request.method == 'POST':
            nom = request.form.get('nom')
            description = request.form.get('description', '')
            couleur = request.form.get('couleur', '#6366f1')
            icone = request.form.get('icone', 'fa-user-shield')
            priorite = request.form.get('priorite', 0)
            permissions = request.form.getlist('permissions')

            conn = get_db_connection()
            if not conn:
                flash('Erreur de connexion à la base de données.', 'danger')
                return redirect(url_for('admin_roles'))

            try:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE admin_roles
                    SET nom = %s, description = %s, couleur = %s, icone = %s, priorite = %s
                    WHERE id = %s
                ''', (nom, description, couleur, icone, priorite, role_id))
                conn.commit()

                # Mettre à jour les permissions
                PermissionManager.update_role_permissions(role_id, permissions)

                flash(f'Rôle "{nom}" modifié avec succès.', 'success')
            except Error as e:
                flash(f'Erreur lors de la modification du rôle: {e}', 'danger')
            finally:
                cursor.close()
                conn.close()

            return redirect(url_for('admin_roles'))

        # GET: Afficher le formulaire d'édition
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_roles'))

        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM admin_roles WHERE id = %s', (role_id,))
        role = cursor.fetchone()
        cursor.close()
        conn.close()

        if not role:
            flash('Rôle non trouvé.', 'danger')
            return redirect(url_for('admin_roles'))

        role['permissions'] = PermissionManager.get_role_permissions(role_id)
        permissions = PermissionManager.get_all_permissions()

        return render_template('admin_edit_role.html',
                             role=role,
                             permissions=permissions,
                             modules_labels={
                                 'cours': 'Cours',
                                 'finance': 'Finances',
                                 'notes': 'Notes',
                                 'presences': 'Présences',
                                 'users': 'Utilisateurs',
                                 'classes': 'Classes',
                                 'stats': 'Statistiques',
                                 'system': 'Système'
                             })


    @app.route('/admin/roles/<int:role_id>/delete', methods=['POST'])
    @login_required
    @admin_required
    @require_permission('users.roles')
    def delete_role(role_id):
        """Supprimer un rôle"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_roles'))

        try:
            cursor = conn.cursor(dictionary=True)

            # Vérifier si le rôle est utilisé
            cursor.execute('SELECT COUNT(*) as count FROM users WHERE admin_role_id = %s AND school_id = %s', (role_id, tenant.current_school_id()))
            result = cursor.fetchone()
            if result['count'] > 0:
                flash('Impossible de supprimer ce rôle car il est attribué à des utilisateurs.', 'danger')
                return redirect(url_for('admin_roles'))

            # Récupérer le nom du rôle
            cursor.execute('SELECT nom FROM admin_roles WHERE id = %s', (role_id,))
            role = cursor.fetchone()

            # Supprimer le rôle
            cursor.execute('DELETE FROM admin_roles WHERE id = %s', (role_id,))
            conn.commit()

            flash(f'Rôle "{role["nom"]}" supprimé avec succès.', 'success')
        except Error as e:
            flash(f'Erreur lors de la suppression du rôle: {e}', 'danger')
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for('admin_roles'))


    @app.route('/admin/users/<int:user_id>/assign_role', methods=['POST'])
    @login_required
    @admin_required
    @require_permission('users.roles')
    def assign_user_role(user_id):
        """Attribuer un rôle à un utilisateur"""
        role_id = request.form.get('role_id')

        if role_id:
            role_id = int(role_id) if role_id != '' else None
        else:
            role_id = None

        if PermissionManager.assign_role_to_user(user_id, role_id):
            flash('Rôle attribué avec succès.', 'success')
        else:
            flash('Erreur lors de l\'attribution du rôle.', 'danger')

        return redirect(request.referrer or url_for('admin_roles'))


    @app.route('/admin/roles/users')
    @login_required
    @admin_required
    @require_permission('users.roles')
    def admin_roles_users():
        """Liste des administrateurs avec leurs rôles"""
        conn = get_db_connection()
        if not conn:
            flash('Erreur de connexion à la base de données.', 'danger')
            return redirect(url_for('admin_dashboard'))

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT u.id, u.nom, u.prenom, u.email, u.admin_role_id,
                       r.nom as role_nom, r.couleur as role_couleur, r.icone as role_icone
                FROM users u
                LEFT JOIN admin_roles r ON u.admin_role_id = r.id
                WHERE u.role = 'admin' AND u.school_id = %s
                ORDER BY r.priorite DESC, u.nom
            ''', (tenant.current_school_id(),))
            admins = cursor.fetchall()

            roles = PermissionManager.get_all_roles()

            return render_template('admin_roles_users.html', admins=admins, roles=roles)
        except Error as e:
            flash(f'Erreur: {e}', 'danger')
            return redirect(url_for('admin_dashboard'))
        finally:
            cursor.close()
            conn.close()


    @app.route('/api/admin/permissions/check/<permission_code>')
    @login_required
    @admin_required
    def api_check_permission(permission_code):
        """API pour vérifier une permission"""
        user_id = session.get('user_id')
        has_perm = PermissionManager.has_permission(user_id, permission_code)
        return jsonify({'has_permission': has_perm, 'permission': permission_code})
