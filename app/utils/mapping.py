from datetime import date

def normalize_release_date(s: str | None, precision: str | None):
    if not s:
        return None
    if precision == "day":
        return date.fromisoformat(s)
    if precision == "month":
        return date.fromisoformat(f"{s}-01")
    if precision == "year":
        return date.fromisoformat(f"{s}-01-01")
    try:
        return date.fromisoformat(s)
    except Exception:
        return None
