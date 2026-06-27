"""
Module de gestion des permissions pour les administrateurs
Système RBAC (Role-Based Access Control) professionnel
"""

from functools import wraps
from flask import session, redirect, url_for, flash, abort, g
import mysql.connector
from mysql.connector import Error

# Configuration de la base de données
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'adsclass_bd',
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci'
}

def get_db_connection():
    """Obtenir une connexion à la base de données"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"Erreur de connexion à MySQL: {e}")
        return None


class PermissionManager:
    """Gestionnaire de permissions pour les administrateurs"""
    
    _cache = {}  # Cache des permissions par utilisateur
    
    @classmethod
    def clear_cache(cls, user_id=None):
        """Vider le cache des permissions"""
        if user_id:
            cls._cache.pop(user_id, None)
        else:
            cls._cache.clear()
    
    @classmethod
    def get_user_permissions(cls, user_id):
        """Récupérer toutes les permissions d'un utilisateur"""
        if user_id in cls._cache:
            return cls._cache[user_id]
        
        conn = get_db_connection()
        if not conn:
            return set()
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT DISTINCT p.code
                FROM admin_permissions p
                JOIN admin_role_permissions rp ON p.id = rp.permission_id
                JOIN admin_roles r ON rp.role_id = r.id
                JOIN users u ON u.admin_role_id = r.id
                WHERE u.id = %s AND r.actif = TRUE
            ''', (user_id,))
            
            permissions = {row['code'] for row in cursor.fetchall()}
            cls._cache[user_id] = permissions
            return permissions
        except Error as e:
            print(f"Erreur lors de la récupération des permissions: {e}")
            return set()
        finally:
            cursor.close()
            conn.close()
    
    @classmethod
    def has_permission(cls, user_id, permission_code):
        """Vérifier si un utilisateur a une permission spécifique"""
        permissions = cls.get_user_permissions(user_id)
        return permission_code in permissions
    
    @classmethod
    def has_any_permission(cls, user_id, permission_codes):
        """Vérifier si un utilisateur a au moins une des permissions"""
        permissions = cls.get_user_permissions(user_id)
        return bool(permissions.intersection(permission_codes))
    
    @classmethod
    def has_all_permissions(cls, user_id, permission_codes):
        """Vérifier si un utilisateur a toutes les permissions"""
        permissions = cls.get_user_permissions(user_id)
        return set(permission_codes).issubset(permissions)
    
    @classmethod
    def get_user_role(cls, user_id):
        """Récupérer le rôle d'un utilisateur"""
        conn = get_db_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT r.id, r.nom, r.description, r.couleur, r.icone, r.priorite
                FROM admin_roles r
                JOIN users u ON u.admin_role_id = r.id
                WHERE u.id = %s AND r.actif = TRUE
            ''', (user_id,))
            return cursor.fetchone()
        except Error as e:
            print(f"Erreur lors de la récupération du rôle: {e}")
            return None
        finally:
            cursor.close()
            conn.close()
    
    @classmethod
    def _resolve_school_id(cls, school_id):
        """Résoudre l'école courante si non fournie (multi-tenant)."""
        if school_id is not None:
            return school_id
        import tenant
        return tenant.current_school_id()

    @classmethod
    def get_all_roles(cls, school_id=None):
        """Récupérer tous les rôles actifs de l'école courante (multi-tenant)"""
        school_id = cls._resolve_school_id(school_id)
        conn = get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT id, nom, description, couleur, icone, priorite, is_system, school_id
                FROM admin_roles
                WHERE actif = TRUE AND school_id = %s
                ORDER BY priorite DESC
            ''', (school_id,))
            return cursor.fetchall()
        except Error as e:
            print(f"Erreur lors de la récupération des rôles: {e}")
            return []
        finally:
            cursor.close()
            conn.close()
    
    @classmethod
    def get_archived_roles(cls, school_id=None):
        """Récupérer tous les rôles archivés (actif=0) de l'école courante (multi-tenant)"""
        school_id = cls._resolve_school_id(school_id)
        conn = get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT id, nom, description, couleur, icone, priorite, is_system, school_id
                FROM admin_roles
                WHERE actif = FALSE AND school_id = %s
                ORDER BY priorite DESC
            ''', (school_id,))
            return cursor.fetchall()
        except Error as e:
            print(f"Erreur lors de la récupération des rôles archivés: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_all_permissions(cls):
        """Récupérer toutes les permissions groupées par module"""
        conn = get_db_connection()
        if not conn:
            return {}
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT id, code, nom, description, module
                FROM admin_permissions
                ORDER BY module, nom
            ''')
            
            permissions = {}
            for row in cursor.fetchall():
                module = row['module']
                if module not in permissions:
                    permissions[module] = []
                permissions[module].append(row)
            return permissions
        except Error as e:
            print(f"Erreur lors de la récupération des permissions: {e}")
            return {}
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_role_permissions(cls, role_id):
        """Récupérer les permissions d'un rôle"""
        conn = get_db_connection()
        if not conn:
            return set()

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('''
                SELECT p.code
                FROM admin_permissions p
                JOIN admin_role_permissions rp ON p.id = rp.permission_id
                WHERE rp.role_id = %s
            ''', (role_id,))
            return {row['code'] for row in cursor.fetchall()}
        except Error as e:
            print(f"Erreur lors de la récupération des permissions du rôle: {e}")
            return set()
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def update_role_permissions(cls, role_id, permission_codes):
        """Mettre à jour les permissions d'un rôle"""
        conn = get_db_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()

            # Supprimer les anciennes permissions
            cursor.execute('DELETE FROM admin_role_permissions WHERE role_id = %s', (role_id,))

            # Ajouter les nouvelles permissions
            cursor.execute('SELECT id, code FROM admin_permissions')
            perm_ids = {code: id for id, code in cursor.fetchall()}

            for code in permission_codes:
                if code in perm_ids:
                    cursor.execute('''
                        INSERT INTO admin_role_permissions (role_id, permission_id)
                        VALUES (%s, %s)
                    ''', (role_id, perm_ids[code]))

            conn.commit()
            cls.clear_cache()  # Vider le cache
            return True
        except Error as e:
            print(f"Erreur lors de la mise à jour des permissions: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def assign_role_to_user(cls, user_id, role_id, school_id=None):
        """Attribuer un rôle à un utilisateur (scopé à l'école courante)"""
        school_id = cls._resolve_school_id(school_id)
        conn = get_db_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE users SET admin_role_id = %s WHERE id = %s AND school_id = %s',
                (role_id, user_id, school_id),
            )
            conn.commit()
            cls.clear_cache(user_id)
            return True
        except Error as e:
            print(f"Erreur lors de l'attribution du rôle: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def bulk_assign_role(cls, user_ids, role_id, school_id=None):
        """Attribuer un rôle à plusieurs utilisateurs de l'école courante. Retourne (nb, erreur)."""
        school_id = cls._resolve_school_id(school_id)
        ids = [int(uid) for uid in user_ids if str(uid).strip()]
        if not ids:
            return 0, None
        conn = get_db_connection()
        if not conn:
            return 0, "Erreur de connexion à la base de données."
        try:
            cursor = conn.cursor()
            placeholders = ','.join(['%s'] * len(ids))
            cursor.execute(
                f'UPDATE users SET admin_role_id = %s WHERE id IN ({placeholders}) AND school_id = %s',
                (role_id, *ids, school_id),
            )
            conn.commit()
            count = cursor.rowcount
            cls.clear_cache()
            return count, None
        except Error as e:
            conn.rollback()
            return 0, str(e)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_role(cls, role_id, school_id=None):
        """Récupérer un rôle de l'école courante (multi-tenant). None si absent/autre école."""
        school_id = cls._resolve_school_id(school_id)
        conn = get_db_connection()
        if not conn:
            return None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                'SELECT * FROM admin_roles WHERE id = %s AND school_id = %s',
                (role_id, school_id),
            )
            return cursor.fetchone()
        except Error as e:
            print(f"Erreur lors de la récupération du rôle: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def create_role(cls, nom, description='', couleur='#6366f1', icone='fa-user-shield',
                    priorite=0, permission_codes=None, school_id=None):
        """Créer un rôle personnalisé dans l'école courante. Retourne (role_id, erreur)."""
        school_id = cls._resolve_school_id(school_id)
        conn = get_db_connection()
        if not conn:
            return None, "Erreur de connexion à la base de données."
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO admin_roles (nom, description, couleur, icone, priorite, school_id, is_system)
                VALUES (%s, %s, %s, %s, %s, %s, 0)
            ''', (nom, description, couleur, icone, priorite, school_id))
            role_id = cursor.lastrowid
            conn.commit()
            if permission_codes:
                cls.update_role_permissions(role_id, permission_codes)
            return role_id, None
        except Error as e:
            conn.rollback()
            return None, str(e)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def update_role(cls, role_id, nom, description='', couleur='#6366f1', icone='fa-user-shield',
                    priorite=0, permission_codes=None, school_id=None):
        """Modifier un rôle de l'école courante. Retourne (ok, erreur)."""
        school_id = cls._resolve_school_id(school_id)
        role = cls.get_role(role_id, school_id)
        if not role:
            return False, "Rôle introuvable dans cette école."
        conn = get_db_connection()
        if not conn:
            return False, "Erreur de connexion à la base de données."
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE admin_roles
                SET nom = %s, description = %s, couleur = %s, icone = %s, priorite = %s
                WHERE id = %s AND school_id = %s
            ''', (nom, description, couleur, icone, priorite, role_id, school_id))
            conn.commit()
            cls.update_role_permissions(role_id, permission_codes or [])
            return True, None
        except Error as e:
            conn.rollback()
            return False, str(e)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def duplicate_role(cls, role_id, school_id=None):
        """Dupliquer un rôle (copie personnalisée non-système) dans la même école. Retourne (new_id, erreur)."""
        school_id = cls._resolve_school_id(school_id)
        src = cls.get_role(role_id, school_id)
        if not src:
            return None, "Rôle introuvable dans cette école."
        base_nom = f"{src['nom']} (copie)"
        nom = base_nom
        suffix = 2
        while cls._role_name_exists(nom, school_id):
            nom = f"{base_nom} {suffix}"
            suffix += 1
        perms = cls.get_role_permissions(role_id)
        return cls.create_role(
            nom, src.get('description') or '', src.get('couleur') or '#6366f1',
            src.get('icone') or 'fa-user-shield', src.get('priorite') or 0,
            list(perms), school_id,
        )

    @classmethod
    def _role_name_exists(cls, nom, school_id):
        conn = get_db_connection()
        if not conn:
            return False
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT 1 FROM admin_roles WHERE nom = %s AND school_id = %s LIMIT 1',
                (nom, school_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def archive_role(cls, role_id, archived=True, school_id=None):
        """Archiver (actif=0) ou réactiver (actif=1) un rôle de l'école courante. Retourne (ok, erreur)."""
        school_id = cls._resolve_school_id(school_id)
        role = cls.get_role(role_id, school_id)
        if not role:
            return False, "Rôle introuvable dans cette école."
        conn = get_db_connection()
        if not conn:
            return False, "Erreur de connexion à la base de données."
        try:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE admin_roles SET actif = %s WHERE id = %s AND school_id = %s',
                (0 if archived else 1, role_id, school_id),
            )
            conn.commit()
            cls.clear_cache()
            return True, None
        except Error as e:
            conn.rollback()
            return False, str(e)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def delete_role(cls, role_id, school_id=None):
        """Supprimer un rôle de l'école courante. Bloque les rôles système et ceux attribués. Retourne (ok, message)."""
        school_id = cls._resolve_school_id(school_id)
        role = cls.get_role(role_id, school_id)
        if not role:
            return False, "Rôle introuvable dans cette école."
        if role.get('is_system'):
            return False, "Impossible de supprimer un rôle système."
        conn = get_db_connection()
        if not conn:
            return False, "Erreur de connexion à la base de données."
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                'SELECT COUNT(*) AS count FROM users WHERE admin_role_id = %s AND school_id = %s',
                (role_id, school_id),
            )
            if cursor.fetchone()['count'] > 0:
                return False, "Impossible de supprimer ce rôle car il est attribué à des utilisateurs."
            cursor.execute(
                'DELETE FROM admin_roles WHERE id = %s AND school_id = %s',
                (role_id, school_id),
            )
            conn.commit()
            cls.clear_cache()
            return True, role['nom']
        except Error as e:
            conn.rollback()
            return False, str(e)
        finally:
            cursor.close()
            conn.close()


# === DÉCORATEURS DE PERMISSION ===

def require_permission(permission_code):
    """Décorateur pour exiger une permission spécifique"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Veuillez vous connecter.', 'warning')
                return redirect(url_for('login'))

            if session.get('role') != 'admin':
                flash('Accès réservé aux administrateurs.', 'danger')
                return redirect(url_for('login'))

            user_id = session['user_id']
            if not PermissionManager.has_permission(user_id, permission_code):
                flash(f'Vous n\'avez pas la permission requise: {permission_code}', 'danger')
                return redirect(url_for('admin_dashboard'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_any_permission(*permission_codes):
    """Décorateur pour exiger au moins une des permissions"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Veuillez vous connecter.', 'warning')
                return redirect(url_for('login'))

            if session.get('role') != 'admin':
                flash('Accès réservé aux administrateurs.', 'danger')
                return redirect(url_for('login'))

            user_id = session['user_id']
            if not PermissionManager.has_any_permission(user_id, permission_codes):
                flash('Vous n\'avez pas les permissions requises.', 'danger')
                return redirect(url_for('admin_dashboard'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_all_permissions(*permission_codes):
    """Décorateur pour exiger toutes les permissions"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Veuillez vous connecter.', 'warning')
                return redirect(url_for('login'))

            if session.get('role') != 'admin':
                flash('Accès réservé aux administrateurs.', 'danger')
                return redirect(url_for('login'))

            user_id = session['user_id']
            if not PermissionManager.has_all_permissions(user_id, permission_codes):
                flash('Vous n\'avez pas toutes les permissions requises.', 'danger')
                return redirect(url_for('admin_dashboard'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# === FONCTIONS UTILITAIRES POUR LES TEMPLATES ===

def check_permission(permission_code):
    """Vérifier une permission dans les templates Jinja2"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return False
    return PermissionManager.has_permission(session['user_id'], permission_code)


def get_current_user_role():
    """Récupérer le rôle de l'utilisateur courant"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return None
    return PermissionManager.get_user_role(session['user_id'])


def get_current_user_permissions():
    """Récupérer les permissions de l'utilisateur courant"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return set()
    return PermissionManager.get_user_permissions(session['user_id'])
