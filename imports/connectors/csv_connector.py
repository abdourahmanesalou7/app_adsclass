"""
Connecteur CSV / TSV avec détection auto du séparateur et encodage.
"""
import csv
from .base import BaseConnector
from ..security import sanitize_cell, enforce_row_limit, enforce_column_limit


def _open_text(filepath):
    """Tente plusieurs encodages courants."""
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=enc, newline='') as f:
                f.read(2048)
            return enc
        except UnicodeDecodeError:
            continue
    return 'utf-8'


class CSVConnector(BaseConnector):
    source_type = 'csv'

    def extract(self, filepath, delimiter=None, header_row=1, **kwargs):
        encoding = _open_text(filepath)
        with open(filepath, 'r', encoding=encoding, newline='') as f:
            sample = f.read(4096)
            f.seek(0)

            if delimiter:
                dialect_delim = delimiter
            else:
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=[',', ';', '\t', '|'])
                    dialect_delim = dialect.delimiter
                except csv.Error:
                    dialect_delim = ','

            reader = csv.reader(f, delimiter=dialect_delim)
            all_rows = list(reader)

        if not all_rows:
            return {'headers': [], 'rows': []}

        header_idx = max(0, header_row - 1)
        headers = self._normalize_headers(all_rows[header_idx] if header_idx < len(all_rows) else [])
        enforce_column_limit(headers)

        data_rows = []
        for row in all_rows[header_idx + 1:]:
            if not row or all((c is None or not str(c).strip()) for c in row):
                continue
            record = {}
            for i, header in enumerate(headers):
                val = row[i] if i < len(row) else None
                record[header] = sanitize_cell(val)
            data_rows.append(record)

        enforce_row_limit(len(data_rows))
        return {
            'headers': headers,
            'rows': data_rows,
            'meta': {'delimiter': dialect_delim, 'encoding': encoding},
        }
