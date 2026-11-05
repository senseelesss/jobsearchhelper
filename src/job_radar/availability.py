def is_expired(row) -> bool:
    return (
        row.get("fetch_status") == "expired"
        or str(row.get("fetch_error", "")).casefold() == "expired listing"
        or row.get("http_status") in {404, 410}
    )


def group_expired(rows) -> bool:
    return bool(rows) and all(is_expired(row) for row in rows)
