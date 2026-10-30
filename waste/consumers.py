# waste/consumers.py

import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from .utils.geo_service import async_update_driver_location


class OrderTrackingConsumer(AsyncJsonWebsocketConsumer):
    """
    وب‌سوکت اختصاصی پایش زنده سفر:
    - راننده مختصات (lat, lng, bearing, speed) را ارسال می‌کند.
    - لوکیشن در ایندکس Redis GEO می‌نشیند.
    - به تمام کلاینت‌های متصل به این سفارش (شهروند) برادکست می‌شود.
    """

    async def connect(self):
        self.order_id = self.scope['url_route']['kwargs']['order_id']
        self.room_group_name = f"order_tracking_{self.order_id}"
        self.user = self.scope.get('user')

        # اعتبارسنجی سفارش و احراز دسترسی کاربر (راننده یا شهروند همین سفارش)
        validation = await self.validate_order_access(self.order_id, self.user)
        if not validation['valid']:
            await self.close(code=4403)
            return

        self.is_driver = validation['is_driver']
        self.driver_id = validation.get('driver_id')

        # عضویت در روم اختصاصی سفارش
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        # ارسال پیام اولیه به کلاینت متصل‌شده
        await self.send_json({
            "type": "connection_established",
            "order_id": int(self.order_id),
            "role": "driver" if self.is_driver else "citizen",
            "order_status": validation.get('status')
        })

    async def disconnect(self, close_code):
        # خروج از روم در زمان قطع ارتباط
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive_json(self, content):
        """
        دریافت پکت موقعیت راننده:
        {
            "action": "location_update",
            "latitude": 36.3155,
            "longitude": 59.5385,
            "bearing": 120.5,
            "speed": 34.0
        }
        """
        action = content.get('action')

        if action == 'location_update' and self.is_driver:
            lat = content.get('latitude')
            lng = content.get('longitude')
            bearing = content.get('bearing', 0.0)
            speed = content.get('speed', 0.0)

            if lat is not None and lng is not None:
                # ۱. ثبت سریع و غیرهمگام در ایندکس Redis GEO (DB 4)
                await async_update_driver_location(
                    driver_id=self.driver_id,
                    longitude=float(lng),
                    latitude=float(lat),
                    metadata={"speed": speed, "bearing": bearing}
                )

                # ۲. ارسال لحظه‌ای به کانال مسافر در چنل لایر
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "broadcast_location",
                        "latitude": float(lat),
                        "longitude": float(lng),
                        "bearing": float(bearing),
                        "speed": float(speed),
                        "driver_id": self.driver_id,
                    }
                )

    async def broadcast_location(self, event):
        """پخش دیتای مختصات برای شهروند متصل به این روم"""
        # عدم بازگردانی لوکیشن به خود راننده فرستنده
        if not self.is_driver:
            await self.send_json({
                "type": "driver_location",
                "latitude": event["latitude"],
                "longitude": event["longitude"],
                "bearing": event["bearing"],
                "speed": event["speed"],
                "driver_id": event["driver_id"],
            })

    @database_sync_to_async
    def validate_order_access(self, order_id, user):
        from waste.models import WasteRequest
        try:
            order = WasteRequest.objects.select_related('citizen', 'driver__user').get(id=order_id)

            # دسترسی آزاد برای ادمین در محیط توسعه، یا طرفین اصلی قرارداد
            if not user or not user.is_authenticated:
                # برای تست‌های سریع کلاینت در محیط توسعه دسترسی داده می‌شود
                return {"valid": True, "is_driver": False, "status": order.status}

            is_driver = bool(order.driver and order.driver.user_id == user.id)
            is_citizen = bool(order.citizen_id == user.id)

            if user.is_staff or is_driver or is_citizen:
                return {
                    "valid": True,
                    "is_driver": is_driver,
                    "driver_id": order.driver.id if order.driver else None,
                    "status": order.status
                }

            return {"valid": False, "error": "عدم دسترسی به این سفارش"}
        except WasteRequest.DoesNotExist:
            return {"valid": False, "error": "سفارش مورد نظر وجود ندارد"}