"""Décorateurs d'accès pour le portail superadmin."""
from functools import wraps
from flask import session, redirect, url_for, flash


def superadmin_required(view):
    """Bloque l'accès à toute vue à un user qui n'est pas superadmin."""
    @wraps(view)
    def _wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'superadmin':
            flash("Accès réservé au superadmin.", "danger")
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return _wrapped
