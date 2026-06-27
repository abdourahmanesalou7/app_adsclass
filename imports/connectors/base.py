"""
Interface commune des connecteurs.
"""
from abc import ABC, abstractmethod


class BaseConnector(ABC):
    """
    Tous les connecteurs renvoient une structure pivot :
        {
            'headers': [str, ...],
            'rows': [ {header: value, ...}, ... ]
        }
    """
    source_type = None

    @abstractmethod
    def extract(self, **kwargs):
        ...

    @staticmethod
    def _normalize_headers(headers):
        out = []
        seen = {}
        for h in headers:
            h_clean = (str(h).strip() if h is not None else '').replace('\xa0', ' ')
            if not h_clean:
                h_clean = 'colonne_inconnue'
            base = h_clean
            i = seen.get(base, 0)
            if i:
                h_clean = f"{base}_{i+1}"
            seen[base] = i + 1
            out.append(h_clean)
        return out
