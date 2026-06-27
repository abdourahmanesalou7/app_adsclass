"""Blueprint /superadmin/* — portail de pilotage cross-tenant."""
from datetime import datetime, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, abort, jsonify
)
from werkzeug.security import generate_password_hash

from db import get_db_connection
from services import subscriptions
from .auth import superadmin_required
from . import queries
from . import provisioning

bp = Blueprint('superadmin', __name__, url_prefix='/superadmin')


# ---------- Dashboard ----------
@bp.route('/')
@superadmin_required
def dashboard():
    return render_template(
        'superadmin/dashboard.html',
        kpis=queries.kpis_global(),
        recent=queries.recent_signups(8),
    )


# ---------- Écoles ----------
@bp.route('/schools')
@superadmin_required
def schools_list():
    return render_template('superadmin/schools_list.html',
                           schools=queries.list_schools_full())


@bp.route('/schools/new', methods=['GET', 'POST'])
@superadmin_required
def school_new():
    if request.method == 'POST':
        f = request.form
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO schools
                  (nom, code, domaine, pays, ville, adresse, telephone,
                   email_contact, devise, statut)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                f.get('nom', '').strip(),
                (f.get('code') or '').strip().upper() or None,
                (f.get('domaine') or '').strip() or None,
                f.get('pays') or 'Niger',
                f.get('ville') or None,
                f.get('adresse') or None,
                f.get('telephone') or None,
                f.get('email_contact') or None,
                f.get('devise') or 'XOF',
                f.get('statut') or 'trial',
            ))
            new_id = cur.lastrowid
            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Erreur création école : {e}", "danger")
            return redirect(url_for('superadmin.school_new'))
        finally:
            cur.close()
            conn.close()

        # Création automatique d'un abonnement Trial si demandé
        plan_code = f.get('initial_plan')
        if plan_code:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("SELECT id FROM subscription_plans WHERE code=%s", (plan_code,))
                row = cur.fetchone()
                if row:
                    subscriptions.create_subscription(
                        new_id, row['id'], duration_days=int(f.get('duration_days') or 30),
                        status='trial' if plan_code == 'TRIAL' else 'active',
                        created_by=session.get('user_id'),
                        notes=f"Abonnement initial créé par superadmin",
                    )
            finally:
                cur.close()
                conn.close()

        flash(f"École « {f.get('nom')} » créée (id={new_id}).", "success")

        # Provisionnement automatique du compte Directeur si demandé
        if f.get('create_director'):
            school = queries.get_school(new_id)
            creds, err = provisioning.create_school_director(
                school,
                email=(f.get('director_email') or '').strip() or None,
                prenom=(f.get('director_prenom') or '').strip() or None,
                nom=(f.get('director_nom') or '').strip() or None,
            )
            if creds:
                flash(
                    f"Directeur créé : {creds['email']} / {creds['password']} "
                    f"(identifiant {creds['identifiant']}).", "success")
            else:
                flash(f"École créée mais directeur non provisionné : {err}", "warning")

        return redirect(url_for('superadmin.school_detail', school_id=new_id))

    return render_template('superadmin/school_form.html',
                           school=None, plans=subscriptions.list_plans())


@bp.route('/schools/<int:school_id>')
@superadmin_required
def school_detail(school_id):
    school = queries.get_school(school_id)
    if not school:
        abort(404)
    return render_template(
        'superadmin/school_detail.html',
        school=school,
        subscription=subscriptions.get_active_subscription(school_id),
        sub_history=queries.list_subscriptions_for(school_id),
        payments=queries.list_payments_for(school_id, 20),
        plans=subscriptions.list_plans(),
        days_left=subscriptions.days_remaining(school_id),
        admins=queries.list_admins_for(school_id),
        suggested_director_email=provisioning.derive_director_email(school),
    )


@bp.route('/schools/<int:school_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def school_edit(school_id):
    school = queries.get_school(school_id)
    if not school:
        abort(404)
    if request.method == 'POST':
        f = request.form
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE schools SET
                  nom=%s, code=%s, domaine=%s, pays=%s, ville=%s,
                  adresse=%s, telephone=%s, email_contact=%s,
                  devise=%s, statut=%s
                WHERE id=%s
            """, (
                f.get('nom', '').strip(),
                (f.get('code') or '').strip().upper() or None,
                (f.get('domaine') or '').strip() or None,
                f.get('pays') or 'Niger',
                f.get('ville') or None,
                f.get('adresse') or None,
                f.get('telephone') or None,
                f.get('email_contact') or None,
                f.get('devise') or 'XOF',
                f.get('statut') or 'active',
                school_id,
            ))
            conn.commit()
            flash("École mise à jour.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Erreur : {e}", "danger")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('superadmin.school_detail', school_id=school_id))
    return render_template('superadmin/school_form.html',
                           school=school, plans=subscriptions.list_plans())


# ---------- Administrateurs / Directeur ----------
@bp.route('/schools/<int:school_id>/director/create', methods=['POST'])
@superadmin_required
def director_create(school_id):
    school = queries.get_school(school_id)
    if not school:
        abort(404)
    f = request.form
    creds, err = provisioning.create_school_director(
        school,
        email=(f.get('director_email') or '').strip() or None,
        prenom=(f.get('director_prenom') or '').strip() or None,
        nom=(f.get('director_nom') or '').strip() or None,
        role_name=(f.get('role_name') or 'Directeur').strip(),
    )
    if creds:
        flash(f"Directeur créé : {creds['email']} / {creds['password']} "
              f"(identifiant {creds['identifiant']}).", "success")
    else:
        flash(f"Création impossible : {err}", "danger")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


@bp.route('/schools/<int:school_id>/admins/<int:user_id>/reset-password', methods=['POST'])
@superadmin_required
def admin_reset_password(school_id, user_id):
    new_pwd = provisioning.reset_user_password(user_id)
    if new_pwd:
        flash(f"Mot de passe réinitialisé : {new_pwd} (changement requis à la connexion).",
              "success")
    else:
        flash("Réinitialisation impossible.", "danger")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


# ---------- Abonnements (actions) ----------
@bp.route('/schools/<int:school_id>/subscription/create', methods=['POST'])
@superadmin_required
def subscription_create(school_id):
    plan_id = int(request.form.get('plan_id') or 0)
    duration = int(request.form.get('duration_days') or 30)
    status = request.form.get('status') or 'active'
    notes = request.form.get('notes') or None
    if not plan_id:
        flash("Plan requis.", "warning")
        return redirect(url_for('superadmin.school_detail', school_id=school_id))
    subscriptions.create_subscription(school_id, plan_id, duration, status,
                                       created_by=session.get('user_id'), notes=notes)
    flash(f"Abonnement créé ({duration} jours).", "success")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


@bp.route('/schools/<int:school_id>/subscription/extend', methods=['POST'])
@superadmin_required
def subscription_extend(school_id):
    days = int(request.form.get('days') or 30)
    if subscriptions.extend_subscription(school_id, days, session.get('user_id')):
        flash(f"+{days} jours ajoutés.", "success")
    else:
        flash("Aucun abonnement à étendre.", "warning")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


@bp.route('/schools/<int:school_id>/subscription/suspend', methods=['POST'])
@superadmin_required
def subscription_suspend(school_id):
    subscriptions.suspend_subscription(school_id)
    flash("Abonnement suspendu.", "warning")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


@bp.route('/schools/<int:school_id>/subscription/reactivate', methods=['POST'])
@superadmin_required
def subscription_reactivate(school_id):
    subscriptions.reactivate_subscription(school_id)
    flash("Abonnement réactivé.", "success")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


@bp.route('/schools/<int:school_id>/subscription/cancel', methods=['POST'])
@superadmin_required
def subscription_cancel(school_id):
    subscriptions.cancel_subscription(school_id)
    flash("Abonnement annulé.", "warning")
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


# ---------- Paiements ----------
@bp.route('/schools/<int:school_id>/payments/new', methods=['POST'])
@superadmin_required
def payment_add(school_id):
    sub = subscriptions.get_active_subscription(school_id)
    if not sub:
        flash("Aucun abonnement actif.", "warning")
        return redirect(url_for('superadmin.school_detail', school_id=school_id))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO subscription_payments
              (subscription_id, school_id, montant, devise, methode,
               reference, statut, created_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,'payé',%s,%s)
        """, (
            sub['id'], school_id,
            float(request.form.get('montant') or 0),
            request.form.get('devise') or 'XOF',
            request.form.get('methode') or 'manuel',
            request.form.get('reference') or None,
            session.get('user_id'),
            request.form.get('notes') or None,
        ))
        cur.execute("UPDATE school_subscriptions SET last_paid_at=NOW(), amount_paid=amount_paid+%s WHERE id=%s",
                    (float(request.form.get('montant') or 0), sub['id']))
        conn.commit()
        flash("Paiement enregistré.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Erreur : {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('superadmin.school_detail', school_id=school_id))


# ---------- Plans ----------
@bp.route('/plans')
@superadmin_required
def plans_list():
    return render_template('superadmin/plans_list.html',
                           plans=subscriptions.list_plans(active_only=False))
