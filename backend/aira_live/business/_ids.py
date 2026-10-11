import hashlib


def doc_id_for(prefix: str, idem_key: str) -> str:
    """Deterministic document id so a retried call writes the same document."""
    return f"{prefix}_{hashlib.sha1(idem_key.encode('utf-8')).hexdigest()[:16]}"
