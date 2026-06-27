"""
Validation ligne par ligne : type + required + max_len + enum.
Retourne (valid: bool, transformed: dict, errors: list[dict]).
"""
from .transformer import transform_value, TransformError
from ..schemas import TRANSFORMS


def validate_row(mapped_row, schema):
    """
    mapped_row : { target_field: raw_value, ... } (post mapping)
    Retour : (is_valid, transformed_dict, errors)
        errors : [ {field, message}, ... ]
    """
    errors = []
    out = {}
    fields = schema['fields']
    fixed = schema.get('fixed_values') or {}

    # Inclure les valeurs fixes
    for k, v in fixed.items():
        out[k] = v

    for fname, fdef in fields.items():
        raw = mapped_row.get(fname)
        # required
        if fdef.get('required') and (raw is None or (isinstance(raw, str) and not raw.strip())):
            errors.append({'field': fname, 'message': 'champ requis manquant'})
            continue
        # transformation
        try:
            value = transform_value(raw, fdef)
        except TransformError as e:
            errors.append({'field': fname, 'message': str(e)})
            continue
        # transform personnalisé (ex: hash_password)
        transform_name = fdef.get('transform')
        if transform_name and value is not None:
            fn = TRANSFORMS.get(transform_name)
            if fn:
                try:
                    value = fn(value)
                except Exception as e:
                    errors.append({'field': fname, 'message': f"transform '{transform_name}' a échoué : {e}"})
                    continue
        out[fname] = value

    return (len(errors) == 0, out, errors)
