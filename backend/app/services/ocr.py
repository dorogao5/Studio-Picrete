import asyncio

import httpx

from app.config import get_settings


class OcrError(Exception):
    pass


async def run_datalab_ocr(filename: str, content: bytes, mime_type: str) -> str:
    settings = get_settings()
    if not settings.datalab_api_key:
        raise OcrError("DATALAB_API_KEY не настроен на сервере Studio")

    headers = {"X-Api-Key": settings.datalab_api_key}
    base = settings.datalab_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=120.0) as client:
        files = {"file": (filename, content, mime_type)}
        data = {"mode": settings.datalab_mode, "output_format": "markdown"}
        response = await client.post(f"{base}/marker", headers=headers, files=files, data=data)
        try:
            body = response.json()
        except Exception as err:
            raise OcrError(f"DataLab вернул не-JSON (HTTP {response.status_code}): {response.text[:300]}") from err
        if response.status_code >= 400 or body.get("success") is False:
            raise OcrError(f"DataLab submit failed (HTTP {response.status_code}): {body.get('error') or body}")

        check_url = body.get("request_check_url")
        if not check_url:
            request_id = body.get("request_id")
            if not request_id:
                raise OcrError(f"DataLab не вернул request_check_url: {body}")
            check_url = f"{base}/marker/{request_id}"

        for _ in range(settings.datalab_max_poll_attempts):
            await asyncio.sleep(settings.datalab_poll_interval_seconds)
            poll = await client.get(check_url, headers=headers)
            poll_body = poll.json()
            status = poll_body.get("status")
            if status == "complete":
                if poll_body.get("success") is False:
                    raise OcrError(f"DataLab OCR failed: {poll_body.get('error')}")
                markdown = poll_body.get("markdown") or ""
                if not markdown:
                    raise OcrError("DataLab вернул пустой markdown")
                return markdown
            if status == "failed":
                raise OcrError(f"DataLab OCR failed: {poll_body.get('error')}")

    raise OcrError("DataLab OCR: превышено время ожидания результата")
