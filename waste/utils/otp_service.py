import logging
import random
import string
import redis
from django.conf import settings

logger = logging.getLogger(__name__)


def get_redis_client():
    """اتصال به دیتابیس ۴ ردیس جهت کش کردن کدهای OTP اکو"""
    try:
        client = redis.Redis(
            host=getattr(settings, 'REDIS_HOST', 'my_redis'),
            port=getattr(settings, 'REDIS_PORT', 6379),
            db=getattr(settings, 'REDIS_OTP_DB', 4),
            decode_responses=True,
            socket_timeout=5,
        )
        client.ping()
        return client
    except redis.exceptions.ConnectionError as e:
        logger.error("❌ اتصال به Redis در سرویس OTP ناموفق بود: %s", e)
        return None


def generate_otp_code(length=5) -> str:
    """تولید کد ۵ رقمی عددی"""
    return ''.join(random.choices(string.digits, k=length))


def save_otp(phone_number: str, otp_code: str, expire_seconds=120) -> bool:
    """ذخیره کد با انقضای ۱۲۰ ثانیه (۲ دقیقه)"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"waste_eco:otp:{phone_number}"
        client.setex(key, expire_seconds, otp_code)
        return True
    except redis.RedisError as e:
        logger.error("خطا در ذخیره OTP ردیس: %s", e)
        return False


def verify_otp(phone_number: str, otp_code: str) -> bool:
    """تطبیق کد ارسالی و پاک‌سازی آنی کلید پس از اعتبارسنجی موفق"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"waste_eco:otp:{phone_number}"
        stored_otp = client.get(key)
        if stored_otp and str(stored_otp).strip() == str(otp_code).strip():
            client.delete(key)
            return True
        return False
    except redis.RedisError as e:
        logger.error("خطا در تطبیق OTP در ردیس: %s", e)
        return False


def get_remaining_time(phone_number: str) -> int:
    """دریافت ثانیه‌های مانده تا منقضی شدن کد جاری"""
    client = get_redis_client()
    if not client:
        return 0
    try:
        key = f"waste_eco:otp:{phone_number}"
        ttl = client.ttl(key)
        return ttl if ttl > 0 else 0
    except redis.RedisError:
        return 0


def check_rate_limit(phone_number: str, max_attempts=5, window_seconds=3600) -> tuple[bool, int, int]:
    """
    محدودیت تعداد درخواست OTP (حداکثر ۵ ریکوئست در ساعت).
    خروجی: (مجاز بودن، تعداد مانده، ثانیه‌های انتظار در صورت لیمیت شدن)
    """
    client = get_redis_client()
    if not client:
        return True, max_attempts, 0

    try:
        key = f"waste_eco:rate_limit:otp:{phone_number}"
        attempts = client.get(key)
        ttl = client.ttl(key)
        wait_sec = ttl if ttl > 0 else window_seconds

        if attempts and int(attempts) >= max_attempts:
            return False, 0, wait_sec

        pipe = client.pipeline()
        pipe.incr(key)
        if not attempts:
            pipe.expire(key, window_seconds)
        pipe.execute()

        current_attempts = int(attempts) + 1 if attempts else 1
        remaining = max(0, max_attempts - current_attempts)
        return True, remaining, 0
    except redis.RedisError as e:
        logger.error("خطای Rate-Limit ردیس: %s", e)
        return True, max_attempts, 0