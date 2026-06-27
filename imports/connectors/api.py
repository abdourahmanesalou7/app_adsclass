"""
Connecteur API REST/JSON.
- URL HTTPS recommandée (HTTP local autorisé)
- Réponse JSON (liste d'objets) ou {data: [...]}
"""
import requests
from urllib.parse import urlparse
from .base import BaseConnector
from ..security import sanitize_cell, enforce_row_limit, enforce_column_limit, ImportSecurityError


class APIConnector(BaseConnector):
    source_type = 'api'

    def extract(self, url, method='GET', headers=None, params=None, data_path=None, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ImportSecurityError("URL doit utiliser http(s)")
        if parsed.hostname is None:
            raise ImportSecurityError("Hôte invalide")

        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                params=params or {},
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ImportSecurityError(f"Appel API échoué : {e}")

        try:
            payload = resp.json()
        except ValueError:
            raise ImportSecurityError("Réponse non JSON")

        # Naviguer dans la réponse si data_path fourni : ex. "result.items"
        rows = payload
        if data_path:
            for key in data_path.split('.'):
                if isinstance(rows, dict) and key in rows:
                    rows = rows[key]
                else:
                    raise ImportSecurityError(f"data_path invalide à '{key}'")

        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ImportSecurityError("Données attendues : liste d'objets JSON")
        if not rows:
            return {'headers': [], 'rows': []}
        if not all(isinstance(r, dict) for r in rows):
            raise ImportSecurityError("Chaque élément doit être un objet")

        all_keys = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)

        headers_list = self._normalize_headers(all_keys)
        enforce_column_limit(headers_list)

        data_rows = []
        for r in rows:
            record = {}
            for orig, norm in zip(all_keys, headers_list):
                v = r.get(orig)
                if isinstance(v, (dict, list)):
                    v = str(v)
                record[norm] = sanitize_cell(v) if isinstance(v, str) else v
            data_rows.append(record)

        enforce_row_limit(len(data_rows))
        return {'headers': headers_list, 'rows': data_rows, 'meta': {'url': url}}
