"""Generic SQL fragment builders.

Small, stateless helpers that return `(clause, params)` pairs for splicing into
a WHERE list. They know nothing about consignors, dates-as-a-domain, or any
particular table — `consignor.py` supplies the tenant-scoped fragments and
these assemble and complement them:

    where, params = and_where(base_clause(scope, "cnr_id"),
                              *date_frags("trip_start", date_from, date_to))
"""


def date_frags(col: str, date_from: str, date_to: str) -> list[tuple[str, list]]:
    """Restrict a DATETIME `col` to the day range [date_from, date_to].

    `date_to` names a *day*, not an instant: it is expanded to end-of-day
    (`< date_to + 1 day`) so a plain YYYY-MM-DD includes that whole day.
    Comparing a DATETIME with `<= 'YYYY-MM-DD'` instead resolves to midnight
    and silently drops the final day — every endpoint must use this helper so
    a trip list and a KPI over the same window always count the same trips.

    Empty strings drop the bound. Output feeds straight into `and_where`.
    """
    frags: list[tuple[str, list]] = []
    if date_from:
        frags.append((f"{col} >= %s", [date_from]))
    if date_to:
        frags.append((f"{col} < DATE_ADD(%s, INTERVAL 1 DAY)", [date_to]))
    return frags


def and_where(*fragments: tuple[str, list]) -> tuple[str, list]:
    """Combine (clause, params) fragments into a single 'WHERE a AND b' string.

    Empty clauses are dropped. Returns ('', []) when nothing applies.
    """
    clauses, params = [], []
    for clause, p in fragments:
        if clause:
            clauses.append(clause)
            params.extend(p)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params
