import logging
import threading
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def normalize_phone_for_meli(phone: str) -> str:
    """نرمال‌سازی شماره موبایل به ساختار استاندارد ۱۱ رقمی ایران (09xxxxxxxxx)"""
    p = phone.strip()
    if p.startswith('+98'):
        p = '0' + p[3:]
    elif p.startswith('98'):
        p = '0' + p[2:]
    elif p.startswith('0098'):
        p = '0' + p[4:]
    if not p.startswith('0'):
        p = '0' + p
    return p


def send_pattern_sms(body_id: int, to: str, args: list[str]) -> dict:
    """ارسال پیامک از خط خدماتی اشتراکی بر مبنای شناسه الگو"""
    safe_phone = normalize_phone_for_meli(to)
    token = getattr(settings, 'MELIPAYAMAK_SHARED_TOKEN', '')
    url = f"https://console.melipayamak.com/api/send/shared/{token}"

    payload = {
        "bodyId": body_id,
        "to": safe_phone,
        "args": args,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            rec_id = result.get("recId")
            logger.info("✅ پیامک الگو ارسال شد: to=%s recId=%s", safe_phone, rec_id)
            return {"status_code": resp.status_code, "result": result, "rec_id": rec_id}
        else:
            logger.warning("⚠️ خطا در پاسخ ملی‌پیامک: to=%s status=%s body=%s", safe_phone, resp.status_code, resp.text)
            return {"status_code": resp.status_code, "result": resp.text, "rec_id": None}
    except Exception as e:
        logger.error("❌ شکست در اتصال به وب‌سرویس پیامک: %s", e)
        return {"status_code": 500, "result": str(e), "rec_id": None}


def send_otp_sms(phone: str, otp_code: str) -> dict:
    """پترن 572362: کد تایید ورود شما به سامانه مدیریت پسماند آریو بنیان توس (اکو) : {0}"""
    body_id = getattr(settings, 'MELIPAYAMAK_BODY_ID_OTP', 572362)
    return send_pattern_sms(body_id, phone, [str(otp_code)])


def send_otp_sms_async(phone: str, otp_code: str):
    """ارسال ناهمگام در نخ مستقل جهت جلوگیری از بلاک شدن چرخه ریکوئست HTTP"""
    def _send():
        result = send_otp_sms(phone, otp_code)
        if result['status_code'] != 200:
            logger.error("خطای ارسال پیامک پس‌زمینه برای %s: %s", phone, result)
        else:
            logger.info("پیامک کد ورود با موفقیت به %s ارسال شد", phone)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()