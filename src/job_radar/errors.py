"""Safe error labels: never serialize third-party exception bodies or requests."""


def error_label(error):
    label = type(error).__name__
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        label += f" (HTTP {status})"
    return label
