"""
Coercitions de type robustes (str/int/float/date/datetime/time/email/enum).
"""
import re
from datetime import datetime, date, time
from decimal import Decimal, InvalidOperation

_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')

DATE_FORMATS = [
    '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d',
    '%d.%m.%Y', '%m/%d/%Y',
]
DATETIME_FORMATS = [
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
    '%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M',
]
TIME_FORMATS = ['%H:%M:%S', '%H:%M']


class TransformError(ValueError):
    pass


def _to_str(v, max_len=None):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if max_len and len(s) > max_len:
        raise TransformError(f"trop long (>{max_len})")
    return s


def _to_int(v):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip().replace(' ', '').replace(',', '.').split('.')[0])
    except (ValueError, AttributeError):
        raise TransformError("entier invalide")


def _to_float(v):
    if v is None or v == '':
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    try:
        s = str(v).strip().replace(' ', '').replace(',', '.')
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        raise TransformError("nombre invalide")


def _to_date(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    # ISO 8601 natif (datetime sérialisé : '2003-03-27T00:00:00', '2003-03-27 00:00:00.123', etc.)
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise TransformError(f"date invalide ({s})")


def _to_datetime(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, time(0, 0))
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        pass
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Fallback : date seule
    try:
        return datetime.combine(_to_date(s), time(0, 0))
    except TransformError:
        raise TransformError(f"datetime invalide ({s})")


def _to_time(v):
    if v is None or v == '':
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = str(v).strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    raise TransformError(f"heure invalide ({s})")


def _to_email(v, max_len=None):
    s = _to_str(v, max_len)
    if s is None:
        return None
    if not _EMAIL_RE.match(s):
        raise TransformError("email invalide")
    return s.lower()


def _to_enum(v, choices):
    if v is None or v == '':
        return None
    s = str(v).strip()
    for c in choices:
        if s.lower() == c.lower():
            return c
    raise TransformError(f"doit être parmi {choices}")


def transform_value(value, field_def):
    """Applique la coercition de type selon field_def du schema."""
    t = field_def['type']
    if t == 'str':
        return _to_str(value, field_def.get('max_len'))
    if t == 'int':
        return _to_int(value)
    if t == 'float':
        return _to_float(value)
    if t == 'date':
        return _to_date(value)
    if t == 'datetime':
        return _to_datetime(value)
    if t == 'time':
        return _to_time(value)
    if t == 'email':
        return _to_email(value, field_def.get('max_len'))
    if t == 'enum':
        return _to_enum(value, field_def.get('choices') or [])
    return value
