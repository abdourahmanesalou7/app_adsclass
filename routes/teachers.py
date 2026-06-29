"""
Module Enseignants — AdsClass
Routes d'administration des professeurs (liste, affectation de classes, identifiants, suppression).
"""

import tenant
from mysql.connector import Error
from student_enrollment_service import get_active_filieres
from permissions import require_permission


def register_teachers_routes(app, deps):
    """Enregistrer les routes d'administration des enseignants sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, flash

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    admin_required = deps['admin_required']
    _init_professeur_classes_table = deps['_init_professeur_classes_table']

    # ============================================================
    # 👨‍🏫 GESTION DES PROFESSEURS
    # ============================================================

    @app.route('/admin/professeurs')
    @login_required
    @admin_required
    @require_permission('users.view')
    def admin_professeurs():
        """Liste des professeurs avec leurs classes assignées."""
        _init_professeur_classes_table()
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion à la base de données.", "danger")
            return redirect(url_for('admin_home'))
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT u.id, u.nom, u.prenom, u.email, u.telephone, u.specialite,
                   u.identifiant, u.password_temp, u.must_change_password,
                   (SELECT COUNT(*) FROM professeur_classes pc WHERE pc.professeur_id = u.id) AS nb_classes,
                   (SELECT COUNT(*) FROM courses c WHERE c.professeur_id = u.id AND COALESCE(c.is_deleted,0)=0) AS nb_cours
            FROM users u
            WHERE u.role = 'professeur' AND u.school_id = %s
            ORDER BY u.nom, u.prenom
        """, (tenant.current_school_id(),))
        professeurs = cursor.fetchall()

        cursor.execute("""
            SELECT pc.professeur_id, pc.filiere_id, pc.niveau, f.nom AS filiere_nom, f.code AS filiere_code
            FROM professeur_classes pc
            JOIN filieres f ON f.id = pc.filiere_id
            WHERE f.school_id = %s
            ORDER BY f.nom, pc.niveau
        """, (tenant.current_school_id(),))
        rows = cursor.fetchall()
        classes_by_prof = {}
        for r in rows:
            classes_by_prof.setdefault(r['professeur_id'], []).append(r)

        filieres = get_active_filieres(cursor, tenant.current_school_id())
        niveaux = ['L1', 'L2', 'L3', 'M1', 'M2']
        conn.close()
        return render_template('admin_professeurs.html',
                               professeurs=professeurs,
                               classes_by_prof=classes_by_prof,
                               filieres=filieres,
                               niveaux=niveaux)

    @app.route('/admin/professeurs/<int:user_id>/assign-classes', methods=['POST'])
    @login_required
    @admin_required
    def admin_professeur_assign_classes(user_id):
        """Remplace les classes assignées à un professeur."""
        _init_professeur_classes_table()
        pairs = request.form.getlist('classes')  # ex: ["1|M2", "3|L3"]
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion.", "danger")
            return redirect(url_for('admin_professeurs'))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE id = %s AND role = 'professeur' AND school_id = %s",
                        (user_id, tenant.current_school_id()))
            if not cur.fetchone():
                flash("Professeur introuvable.", "danger")
                return redirect(url_for('admin_professeurs'))
            cur.execute("DELETE FROM professeur_classes WHERE professeur_id = %s AND school_id = %s",
                        (user_id, tenant.current_school_id()))
            inserted = 0
            for pair in pairs:
                try:
                    fid_str, niv = pair.split('|', 1)
                    fid = int(fid_str)
                    niv = niv.strip()
                    if not niv:
                        continue
                    cur.execute(
                        "INSERT IGNORE INTO professeur_classes (professeur_id, filiere_id, niveau, school_id) VALUES (%s, %s, %s, %s)",
                        (user_id, fid, niv, tenant.current_school_id())
                    )
                    inserted += 1
                except (ValueError, Error):
                    continue
            conn.commit()
            flash(f"{inserted} classe(s) assignée(s).", "success")
        except Error as e:
            conn.rollback()
            flash(f"Erreur : {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('admin_professeurs'))

    @app.route('/admin/professeurs/<int:user_id>/credentials/print')
    @login_required
    @admin_required
    def admin_professeur_credentials_print(user_id):
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion.", "danger")
            return redirect(url_for('admin_professeurs'))
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, nom, prenom, email, identifiant, password_temp, must_change_password,
                   specialite, telephone, role
            FROM users WHERE id = %s AND role = 'professeur' AND school_id = %s
        """, (user_id, tenant.current_school_id()))
        user = cursor.fetchone()
        conn.close()
        if not user:
            flash("Professeur introuvable.", "danger")
            return redirect(url_for('admin_professeurs'))
        return render_template('admin_student_credentials_print.html',
                               students=[user], single=True, nom_classe='',
                               role_label='Professeur')

    @app.route('/admin/professeurs/<int:user_id>/delete', methods=['POST'])
    @login_required
    @admin_required
    def admin_professeur_delete(user_id):
        conn = get_db_connection()
        if not conn:
            flash("Erreur de connexion.", "danger")
            return redirect(url_for('admin_professeurs'))
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM professeur_classes WHERE professeur_id = %s AND school_id = %s",
                        (user_id, tenant.current_school_id()))
            cur.execute("UPDATE courses SET professeur_id = NULL, professeur_nom = NULL WHERE professeur_id = %s AND school_id = %s",
                        (user_id, tenant.current_school_id()))
            cur.execute("DELETE FROM users WHERE id = %s AND role = 'professeur' AND school_id = %s",
                        (user_id, tenant.current_school_id()))
            conn.commit()
            flash("Professeur supprimé.", "success")
        except Error as e:
            conn.rollback()
            flash(f"Suppression impossible : {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('admin_professeurs'))
