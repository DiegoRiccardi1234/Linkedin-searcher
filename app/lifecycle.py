from app.db import Database


def apply_post_scan_lifecycle(db: Database, retention_days: int) -> int:
    """
    Regole richieste:
    - annunci con azione (applied/rejected) non tornano in coda analisi
    - annunci open non preferiti oltre retention vengono archiviati
    - preferiti restano visibili
    """
    return db.cleanup_stale_jobs(retention_days=retention_days)
