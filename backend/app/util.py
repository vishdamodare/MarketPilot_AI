import uuid
from datetime import datetime
from decimal import Decimal


def val(v):
    if isinstance(v, Decimal):
        return str(v)  # money crosses the wire as a string, never a float
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def d(row, *exclude: str) -> dict:
    return {c.key: val(getattr(row, c.key)) for c in row.__mapper__.column_attrs if c.key not in exclude}
