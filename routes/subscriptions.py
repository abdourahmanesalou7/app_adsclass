"""
Routes /subscription/* — Phase 4 SaaS.
Lecture seule pour les écoles ; le superadmin gère la création/extension
depuis le portail dédié (Phase 5).
"""
from flask import Blueprint, render_template, session, redirect, url_for, flash
from services import subscriptions

bp = Blueprint('subscription', __name__, url_prefix='/subscription')


def _require_login():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


@bp.route('/my')
def my_subscription():
    """Vue détaillée de l'abonnement courant de l'école connectée."""
    r = _require_login()
    if r:
        return r
    sub = subscriptions.get_active_subscription()
    days_left = subscriptions.days_remaining() if sub else None
    plans = subscriptions.list_plans()
    return render_template(
        'subscription_my.html',
        subscription=sub,
        days_left=days_left,
        plans=plans,
        is_active=subscriptions.is_active(),
    )


@bp.route('/plans')
def plans_view():
    """Catalogue des plans (consultable même bloqué)."""
    r = _require_login()
    if r:
        return r
    return render_template(
        'subscription_plans.html',
        plans=subscriptions.list_plans(),
        current=subscriptions.get_active_subscription(),
    )


@bp.route('/blocked')
def subscription_blocked():
    """Page affichée quand l'abonnement est expiré/suspendu."""
    sub = None
    days_left = None
    try:
        sub = subscriptions.get_active_subscription()
        days_left = subscriptions.days_remaining() if sub else None
    except Exception:
        pass
    return render_template(
        'subscription_blocked.html',
        subscription=sub,
        days_left=days_left,
    ), 402  # Payment Required
