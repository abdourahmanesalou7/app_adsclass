"""
Context managers pour la gestion de la base de données.

Avantages du context manager:
- Connexion/déconnexion automatique
- Commit/rollback automatique
- Gestion d'erreurs centralisée
- Code plus propre et sûr

Usage:

    # Lecture simple
    from core.database import db_session
    
    with db_session() as (cursor, conn):
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
    # Connexion fermée automatiquement
    
    # Écriture avec commit automatique
    with db_session(commit=True) as (cursor, conn):
        cursor.execute("INSERT INTO users (nom, email) VALUES (%s, %s)", (nom, email))
        user_id = cursor.lastrowid
    # Commit automatique si pas d'erreur
    
    # En cas d'erreur, rollback automatique
    try:
        with db_session(commit=True) as (cursor, conn):
            cursor.execute("INSERT ...")
            raise Exception("Erreur")
    except Exception:
        pass  # Rollback déjà effectué automatiquement
"""

from contextlib import contextmanager
from db import get_db_connection
from core.exceptions import DatabaseError


@contextmanager
def db_session(dictionary=True, commit=False):
    """
    Context manager pour gérer une session de base de données.
    
    Args:
        dictionary (bool): Si True, les résultats sont des dict. Si False, des tuples.
        commit (bool): Si True, commit automatique en fin de bloc sans erreur.
    
    Yields:
        tuple: (cursor, connection)
    
    Raises:
        DatabaseError: Si la connexion échoue
        Exception: Toute exception levée dans le bloc (après rollback)
    
    Examples:
        # Lecture seule
        with db_session() as (cursor, conn):
            cursor.execute("SELECT * FROM users")
            users = cursor.fetchall()
        
        # Écriture avec commit
        with db_session(commit=True) as (cursor, conn):
            cursor.execute("INSERT INTO users (nom) VALUES (%s)", (nom,))
            new_id = cursor.lastrowid
    """
    conn = get_db_connection()
    if conn is None:
        raise DatabaseError("Impossible de se connecter à la base de données")
    
    cursor = conn.cursor(dictionary=dictionary)
    
    try:
        yield cursor, conn
        
        # Si commit=True et pas d'exception, on commit
        if commit:
            conn.commit()
            
    except Exception as e:
        # En cas d'erreur, rollback
        try:
            conn.rollback()
        except Exception:
            pass  # Ignorer les erreurs de rollback
        
        # Re-lever l'exception originale
        raise
        
    finally:
        # Fermer cursor et connexion dans tous les cas
        try:
            cursor.close()
        except Exception:
            pass
        
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def db_transaction():
    """
    Context manager pour une transaction explicite.
    
    Similaire à db_session(commit=True) mais le nom est plus explicite
    pour indiquer qu'on fait une transaction.
    
    Usage:
        with db_transaction() as (cursor, conn):
            cursor.execute("INSERT ...")
            cursor.execute("UPDATE ...")
        # Commit automatique si pas d'erreur
    """
    with db_session(commit=True) as (cursor, conn):
        yield cursor, conn


def execute_query(query, params=None, dictionary=True):
    """
    Exécute une requête SELECT et retourne tous les résultats.
    
    Helper simple pour les cas où on veut juste faire un SELECT.
    
    Args:
        query (str): Requête SQL SELECT
        params (tuple): Paramètres de la requête
        dictionary (bool): Retourner des dict ou des tuples
    
    Returns:
        list: Liste des résultats
    
    Example:
        users = execute_query("SELECT * FROM users WHERE role = %s", ('etudiant',))
    """
    with db_session(dictionary=dictionary) as (cursor, conn):
        cursor.execute(query, params or ())
        return cursor.fetchall()


def execute_one(query, params=None, dictionary=True):
    """
    Exécute une requête SELECT et retourne un seul résultat.
    
    Args:
        query (str): Requête SQL SELECT
        params (tuple): Paramètres de la requête
        dictionary (bool): Retourner un dict ou un tuple
    
    Returns:
        dict | tuple | None: Premier résultat ou None
    
    Example:
        user = execute_one("SELECT * FROM users WHERE id = %s", (user_id,))
    """
    with db_session(dictionary=dictionary) as (cursor, conn):
        cursor.execute(query, params or ())
        return cursor.fetchone()


def execute_update(query, params=None):
    """
    Exécute une requête INSERT/UPDATE/DELETE avec commit automatique.
    
    Args:
        query (str): Requête SQL
        params (tuple): Paramètres de la requête
    
    Returns:
        int: Nombre de lignes affectées
    
    Example:
        rows = execute_update("DELETE FROM users WHERE id = %s", (user_id,))
    """
    with db_session(commit=True) as (cursor, conn):
        cursor.execute(query, params or ())
        return cursor.rowcount
