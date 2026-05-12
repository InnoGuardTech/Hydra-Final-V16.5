from app.core.observability import mark_operation


def mark_browser_ok(operation: str) -> None:
    mark_operation("browser", operation, "ok")
