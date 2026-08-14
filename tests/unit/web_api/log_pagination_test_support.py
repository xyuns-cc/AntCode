from datetime import UTC, datetime
from types import SimpleNamespace


class FakeLogQuery:
    def __init__(self, rows, total):
        self.rows = rows
        self.total = total
        self.filters = []
        self.expressions = []
        self.offset_value = None
        self.limit_value = None

    async def count(self):
        return self.total

    def filter(self, *expressions, **kwargs):
        self.expressions.extend(expressions)
        self.filters.append(kwargs)
        return self

    def order_by(self, *_fields):
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __await__(self):
        async def resolve():
            return self.rows

        return resolve().__await__()


def stored_row(row_id: int, content: str = "line", *, level: str = "INFO", log_type: str = "stdout"):
    return SimpleNamespace(
        id=row_id,
        timestamp=datetime.now(UTC),
        level=level,
        log_type=log_type,
        run_id="run-1",
        content=content,
        source="worker",
        sequence=row_id,
    )


__all__ = ["FakeLogQuery", "stored_row"]
