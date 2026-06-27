"""
Module centralisé de connexion à la base de données MySQL.
Source unique de vérité pour DB_CONFIG et get_db_connection().

Backward-compatible : permissions.py et app.py peuvent continuer à définir
leurs propres helpers qui délèguent ici.
"""
import os
import mysql.connector
from mysql.connector import Error
from contextlib import contextmanager

DB_CONFIG = {
    'host': os.environ.get('ADSCLASS_DB_HOST', 'localhost'),
    'user': os.environ.get('ADSCLASS_DB_USER', 'root'),
    'password': os.environ.get('ADSCLASS_DB_PASSWORD', ''),
    'database': os.environ.get('ADSCLASS_DB_NAME', 'adsclass_bd'),
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
    'autocommit': False,
}


def get_db_connection():
    """Retourne une nouvelle connexion MySQL ou None en cas d'échec."""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"[db] Erreur de connexion MySQL: {e}")
        return None


@contextmanager
def db_cursor(dictionary=True, commit=False):
    """Context manager : ouvre conn + cursor, commit/rollback, ferme tout."""
    conn = get_db_connection()
    if conn is None:
        raise RuntimeError("Connexion MySQL indisponible")
    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield cursor, conn
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.close()
        finally:
            conn.close()


def get_table_columns(table_name):
    """Introspection : retourne la liste des colonnes d'une table (name, type, nullable, key)."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT COLUMN_NAME AS name, DATA_TYPE AS type, IS_NULLABLE AS nullable,
                   COLUMN_KEY AS col_key, COLUMN_DEFAULT AS default_val,
                   CHARACTER_MAXIMUM_LENGTH AS max_length
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (DB_CONFIG['database'], table_name))
        return cursor.fetchall()
    except Error as e:
        print(f"[db] Erreur introspection {table_name}: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def table_exists(table_name):
    """Vrai si la table existe dans la base courante."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """, (DB_CONFIG['database'], table_name))
        return cursor.fetchone()[0] > 0
    except Error:
        return False
    finally:
        cursor.close()
        conn.close()
