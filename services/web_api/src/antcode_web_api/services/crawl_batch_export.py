"""Lossless streaming encoders for crawl batch exports."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Protocol

from fastapi.responses import StreamingResponse

from antcode_web_api.services.crawl_item_stream import CrawlBatchItem
from antcode_web_api.utils.csv_security import sanitize_csv_row

ItemSource = Callable[[str, int | None], AsyncIterator[CrawlBatchItem]]
CSV_COLUMNS = ("sequence", "url", "timestamp", "run_id", "data")


class RowWriter(Protocol):
    def writerow(self, row: Iterable[object], /) -> object: ...


def build_batch_export_response(
    batch_id: str,
    export_format: str,
    item_source: ItemSource,
) -> StreamingResponse:
    """Build an uncapped export response backed by paged item reads."""
    if export_format == "json":
        stream = stream_batch_json(batch_id, item_source)
        media_type = "application/json"
    elif export_format == "csv":
        stream = stream_batch_csv(batch_id, item_source)
        media_type = "text/csv"
    else:
        raise ValueError(f"unsupported crawl batch export format: {export_format}")
    filename = f"batch-{batch_id[:8]}.{export_format}"
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def stream_batch_json(batch_id: str, item_source: ItemSource) -> AsyncIterator[str]:
    yield '{"batch_id":' + json.dumps(batch_id, ensure_ascii=False) + ',"items":['
    count = 0
    async for item in item_source(batch_id, None):
        if count:
            yield ","
        yield json.dumps(_item_payload(item), ensure_ascii=False, separators=(",", ":"))
        count += 1
    yield f'],"count":{count},"truncated":false}}'


async def stream_batch_csv(batch_id: str, item_source: ItemSource) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    yield _write_csv_row(buffer, writer, list(CSV_COLUMNS))
    async for item in item_source(batch_id, None):
        data = json.dumps(item.payload, ensure_ascii=False, separators=(",", ":"))
        row = [item.sequence, item.url, item.timestamp, item.run_id, data]
        yield _write_csv_row(buffer, writer, row)


def _write_csv_row(buffer: io.StringIO, writer: RowWriter, row: list[object]) -> str:
    writer.writerow(sanitize_csv_row(row))
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def _item_payload(item: CrawlBatchItem) -> dict[str, object]:
    return {
        "sequence": item.sequence,
        "url": item.url,
        "timestamp": item.timestamp,
        "run_id": item.run_id,
        "data": item.payload,
    }


__all__ = [
    "CSV_COLUMNS",
    "build_batch_export_response",
    "stream_batch_csv",
    "stream_batch_json",
]
