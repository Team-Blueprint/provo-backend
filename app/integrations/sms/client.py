import logging
import uuid
from typing import Any

import httpx

from app.core.config import settings
from app.core.utils import normalize_phone_number

logger = logging.getLogger(__name__)


class SMSClient:
    """
    Client for dispatching SMS notifications and OTP codes via httpSMS.
    Falls back gracefully to simulated logging when credentials are unset.
    """

    def __init__(self) -> None:
        self.api_key = settings.HTTPSMS_API_KEY
        self.sender_phone = settings.HTTPSMS_SENDER_PHONE
        self.base_url = "https://api.httpsms.com/v1"

    async def send_sms(self, to_phone: str, message: str) -> dict[str, Any]:
        clean_phone = normalize_phone_number(to_phone)

        if not self.api_key or not self.sender_phone:
            logger.info(
                "[SIMULATED SMS] To: %s | Content: %s",
                clean_phone,
                message,
            )
            return {
                "id": f"mock-sms-{uuid.uuid4().hex[:12]}",
                "status": "delivered",
                "simulated": True,
            }

        url = f"{self.base_url}/messages/send"
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        body = {
            "content": message,
            "from": self.sender_phone,
            "to": clean_phone,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                logger.info("Sent SMS to %s via httpSMS (id=%s)", clean_phone, data.get("data", {}).get("id"))
                return data.get("data", {})
        except httpx.HTTPStatusError as e:
            logger.error(
                "httpSMS API error %s for %s: %s",
                e.response.status_code,
                clean_phone,
                e.response.text,
            )
            # Log content for local dev / demo debugging so signup is never blocked by exhausted SMS credits
            logger.warning("[FALLBACK SIMULATED SMS] To: %s | Content: %s", clean_phone, message)
            return {
                "id": f"fallback-sms-{uuid.uuid4().hex[:12]}",
                "status": "failed_provider",
                "simulated": True,
                "error": str(e),
            }
        except Exception as e:
            logger.error("Failed to send SMS to %s: %s", clean_phone, e)
            logger.warning("[FALLBACK SIMULATED SMS] To: %s | Content: %s", clean_phone, message)
            return {
                "id": f"fallback-sms-{uuid.uuid4().hex[:12]}",
                "status": "failed_network",
                "simulated": True,
                "error": str(e),
            }
