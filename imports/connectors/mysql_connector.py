"""
Connecteur MySQL externe : lit depuis une base externe via SELECT.
La requête doit être un SELECT pur (refus de tout DML/DDL).
"""
import re
import mysql.connector
from mysql.connector import Error
from .base import BaseConnector
from ..security import sanitize_cell, enforce_row_limit, enforce_column_limit, ImportSecurityError

_SELECT_RE = re.compile(r'^\s*SELECT\s', re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|RENAME|GRANT|REVOKE|REPLACE|MERGE|CALL|LOAD)\b',
    re.IGNORECASE,
)


class MySQLConnector(BaseConnector):
    source_type = 'mysql'

    def extract(self, host, user, password, database, query, port=3306, **kwargs):
        if not query or not _SELECT_RE.match(query):
            raise ImportSecurityError("Seules les requêtes SELECT sont autorisées")
        if _FORBIDDEN_RE.search(query):
            raise ImportSecurityError("Mots-clés interdits détectés dans la requête")
        if ';' in query.rstrip().rstrip(';'):
            raise ImportSecurityError("Une seule instruction SQL autorisée")

        try:
            conn = mysql.connector.connect(
                host=host, user=user, password=password,
                database=database, port=int(port),
                connection_timeout=10,
            )
        except Error as e:
            raise ImportSecurityError(f"Connexion source impossible : {e}")

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query)
            rows = cursor.fetchall()
        except Error as e:
            raise ImportSecurityError(f"Requête échouée : {e}")
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            conn.close()

        if not rows:
            return {'headers': [], 'rows': []}

        headers = self._normalize_headers(list(rows[0].keys()))
        enforce_column_limit(headers)

        data_rows = []
        for r in rows:
            data_rows.append({h: sanitize_cell(r.get(h)) if isinstance(r.get(h), str) else r.get(h) for h in headers})

        enforce_row_limit(len(data_rows))
        return {'headers': headers, 'rows': data_rows, 'meta': {'source_db': database}}
