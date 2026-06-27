"""
Contrôles de sécurité pour les fichiers uploadés et les payloads d'import.
"""
import os
import re

ALLOWED_EXTENSIONS = {
    'excel': {'.xlsx', '.xlsm', '.xls'},
    'csv':   {'.csv', '.tsv', '.txt'},
    'word':  {'.docx'},
}

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_ROWS_PER_IMPORT = 50_000
MAX_COLUMNS = 80

# Caractères début de cellule à neutraliser (CSV/Excel formula injection)
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


class ImportSecurityError(Exception):
    pass


def validate_filename(filename, source_type):
    """Vérifie extension et nom de fichier."""
    if not filename:
        raise ImportSecurityError("Nom de fichier manquant")
    ext = os.path.splitext(filename)[1].lower()
    allowed = ALLOWED_EXTENSIONS.get(source_type, set())
    if ext not in allowed:
        raise ImportSecurityError(
            f"Extension '{ext}' non autorisée pour {source_type}. "
            f"Autorisées : {', '.join(sorted(allowed))}"
        )
    if re.search(r'[<>:"|?*\x00-\x1f]', filename):
        raise ImportSecurityError("Nom de fichier contient des caractères interdits")
    return ext


def validate_file_size(filepath):
    """Vérifie la taille du fichier physique."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        raise ImportSecurityError("Fichier introuvable")
    if size > MAX_FILE_SIZE_BYTES:
        raise ImportSecurityError(
            f"Fichier trop volumineux ({size} octets > {MAX_FILE_SIZE_BYTES})"
        )
    if size == 0:
        raise ImportSecurityError("Fichier vide")
    return size


def sanitize_cell(value):
    """Neutralise les injections de formule et trim les contrôles."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    cleaned = value.replace('\x00', '').strip()
    if cleaned and cleaned[0] in _FORMULA_PREFIXES:
        cleaned = "'" + cleaned
    if len(cleaned) > 5000:
        cleaned = cleaned[:5000]
    return cleaned


def sanitize_row(row_dict):
    """Applique sanitize_cell à toutes les valeurs d'un dict."""
    return {k: sanitize_cell(v) for k, v in row_dict.items()}


def enforce_row_limit(count):
    if count > MAX_ROWS_PER_IMPORT:
        raise ImportSecurityError(
            f"Trop de lignes ({count} > {MAX_ROWS_PER_IMPORT})"
        )


def enforce_column_limit(headers):
    if len(headers) > MAX_COLUMNS:
        raise ImportSecurityError(
            f"Trop de colonnes ({len(headers)} > {MAX_COLUMNS})"
        )
