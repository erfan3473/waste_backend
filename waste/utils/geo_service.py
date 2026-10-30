# waste/utils/geo_service.py

import json
import logging
import redis
import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

REDIS_HOST = getattr(settings, 'REDIS_HOST', 'my_redis')
REDIS_PORT = getattr(settings, 'REDIS_PORT', 6379)
REDIS_GEO_DB = getattr(settings, 'REDIS_GEO_DB', 4)

# کلیدهای ساختار داده ردیس
DRIVERS_GEO_KEY = "drivers_locations_geo"
DRIVER_META_PREFIX = "driver_meta:"

# ۱. کلاینت اتصال همگام (برای استفاده در Views، APIها و Celery)
sync_redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_GEO_DB,
    decode_responses=True,
    socket_timeout=5,
)

# ۲. استخر اتصال ناهمگام (برای استفاده فوق‌سریع در WebSocket Consumer)
async_redis_pool = aioredis.from_url(
    f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_GEO_DB}",
    decode_responses=True,
    max_connections=20,
)


# ════════════════════════════════════════════════════════════
# 🚀 توابع ناهمگام (مخصوص کانسیومر وب‌سوکت راننده)
# ════════════════════════════════════════════════════════════

async def async_update_driver_location(driver_id: int, longitude: float, latitude: float, metadata: dict = None):
    """
    ۱. ثبت موقعیت مکانی راننده در ایندکس فضایی ردیس با GEOADD
    ۲. ذخیره اطلاعات تکمیلی (نوع خودرو، پلاک، وضعیت) با انقضای ۵ دقیقه
    """
    try:
        member_id = str(driver_id)
        # ثبت در ساختار ردیس جئو: (longitude, latitude, member)
        await async_redis_pool.geoadd(DRIVERS_GEO_KEY, (longitude, latitude, member_id))

        if metadata:
            meta_key = f"{DRIVER_META_PREFIX}{member_id}"
            await async_redis_pool.set(meta_key, json.dumps(metadata, ensure_ascii=False), ex=300)

        return True
    except Exception as e:
        logger.error(f"خطا در ثبت موقعیت ناهمگام راننده {driver_id}: {e}")
        return False


async def async_remove_driver_from_geo(driver_id: int):
    """
    حذف راننده از شاخص مکانی ردیس هنگام آفلاین شدن یا قطع اتصال سوکت
    """
    try:
        member_id = str(driver_id)
        pipe = async_redis_pool.pipeline()
        pipe.zrem(DRIVERS_GEO_KEY, member_id)
        pipe.delete(f"{DRIVER_META_PREFIX}{member_id}")
        await pipe.execute()
        return True
    except Exception as e:
        logger.error(f"خطا در حذف ناهمگام راننده {driver_id} از ردیس: {e}")
        return False


# ════════════════════════════════════════════════════════════
# 🎯 توابع همگام (مخصوص سرویس مچینگ، API شهروند و Celery)
# ════════════════════════════════════════════════════════════

def update_driver_location(driver_id: int, longitude: float, latitude: float, metadata: dict = None):
    """نسخه همگام ثبت موقعیت راننده"""
    try:
        member_id = str(driver_id)
        sync_redis_client.geoadd(DRIVERS_GEO_KEY, (longitude, latitude, member_id))
        if metadata:
            meta_key = f"{DRIVER_META_PREFIX}{member_id}"
            sync_redis_client.set(meta_key, json.dumps(metadata, ensure_ascii=False), ex=300)
        return True
    except Exception as e:
        logger.error(f"خطا در ثبت موقعیت راننده {driver_id}: {e}")
        return False


def remove_driver_from_geo(driver_id: int):
    """نسخه همگام حذف راننده از لیست آنلاین‌ها"""
    try:
        member_id = str(driver_id)
        pipe = sync_redis_client.pipeline()
        pipe.zrem(DRIVERS_GEO_KEY, member_id)
        pipe.delete(f"{DRIVER_META_PREFIX}{member_id}")
        pipe.execute()
        return True
    except Exception as e:
        logger.error(f"خطا در حذف راننده {driver_id} از ردیس: {e}")
        return False


def find_nearby_drivers(longitude: float, latitude: float, radius_km: float = 5.0, limit: int = 10, vehicle_type: str = None):
    """
    یافتن نزدیک‌ترین رانندگان آنلاین در شعاع مشخص با دستور GEOSEARCH
    خروجی: لیستی مرتب‌شده بر اساس کمترین فاصله به بیشترین فاصله
    """
    try:
        # جستجوی اعضا با فاصله و مختصات
        raw_results = sync_redis_client.geosearch(
            DRIVERS_GEO_KEY,
            longitude=longitude,
            latitude=latitude,
            radius=radius_km,
            unit='km',
            withdist=True,
            withcoord=True,
            sort='ASC',
            count=limit * 2 if vehicle_type else limit  # اگر فیلتر نوع خودرو داریم، تعداد بیشتری برای فیلتر کردن می‌گیریم
        )

        if not raw_results:
            return []

        nearby_drivers = []
        for item in raw_results:
            # ساختار خروجی geosearch در redis-py: [member_id, distance_km, (lon, lat)]
            driver_id = int(item[0])
            distance = round(float(item[1]), 2)
            coords = item[2]

            meta_raw = sync_redis_client.get(f"{DRIVER_META_PREFIX}{driver_id}")
            meta = json.loads(meta_raw) if meta_raw else {}

            # فیلتر نوع خودرو در صورت درخواست
            if vehicle_type and meta.get('vehicle_type') != vehicle_type:
                continue

            nearby_drivers.append({
                "driver_id": driver_id,
                "distance_km": distance,
                "longitude": coords[0],
                "latitude": coords[1],
                "vehicle_type": meta.get('vehicle_type'),
                "plate_number": meta.get('plate_number'),
            })

            if len(nearby_drivers) >= limit:
                break

        return nearby_drivers

    except Exception as e:
        logger.error(f"خطا در جستجوی شعاعی رانندگان: {e}")
        return []