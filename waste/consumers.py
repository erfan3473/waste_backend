# waste/consumers.py

import json
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from rest_framework.authtoken.models import Token
from .utils.geo_service import async_update_driver_location


class OrderTrackingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.order_id = self.scope['url_route']['kwargs']['order_id']
        self.room_group_name = f"order_tracking_{self.order_id}"
        
        # استخراج توکن از کوئری‌پارامتر در صورت وجود (مثلاً ?token=xxx)
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        query_params = parse_qs(query_string)
        token_key = query_params.get('token', [None])[0]
        role_param = query_params.get('role', [None])[0]

        user = self.scope.get('user')
        if (not user or not user.is_authenticated) and token_key:
            user = await self.get_user_from_token(token_key)

        self.user = user

        # اعتبارسنجی دسترسی
        validation = await self.validate_order_access(self.order_id, self.user, role_param)
        if not validation['valid']:
            await self.close(code=4403)
            return

        self.is_driver = validation['is_driver']
        self.driver_id = validation.get('driver_id')

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        await self.send_json({
            "type": "connection_established",
            "order_id": int(self.order_id),
            "role": "driver" if self.is_driver else "citizen",
            "order_status": validation.get('status'),
            "driver": validation.get('driver'),
        })

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive_json(self, content):
        """
        دریافت موقعیت راننده با پشتیبانی از هر دو کلید action و type
        """
        action = content.get('action') or content.get('type')

        # بررسی ارسال پکت لوکیشن از سمت راننده
        if action in ['location_update', 'driver_location'] and self.is_driver:
            lat = content.get('latitude')
            lng = content.get('longitude')
            bearing = content.get('bearing', 0.0)
            speed = content.get('speed', 0.0)

            if lat is not None and lng is not None:
                # ۱. ثبت در ایندکس Redis GEO
                await async_update_driver_location(
                    driver_id=self.driver_id,
                    longitude=float(lng),
                    latitude=float(lat),
                    metadata={"speed": speed, "bearing": bearing}
                )

                # ۲. برادکست مستقیم به روم مسافر
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
        if not self.is_driver:
            await self.send_json({
                "type": "driver_location",
                "latitude": event["latitude"],
                "longitude": event["longitude"],
                "bearing": event["bearing"],
                "speed": event["speed"],
                "driver_id": event["driver_id"],
            })

    async def order_driver_assigned(self, event):
        await self.send_json({
            "type": "driver_assigned",
            "status": event["status"],
            "driver": event["driver"],
        })

    async def order_status_changed(self, event):
        await self.send_json({
            "type": "status_update",
            "status": event["status"],
        })

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        try:
            return Token.objects.select_related('user').get(key=token_key).user
        except Token.DoesNotExist:
            return None

    @database_sync_to_async
    def validate_order_access(self, order_id, user, role_param=None):
        from waste.models import WasteRequest
        from waste.serializers import DriverSimpleSerializer
        try:
            order = WasteRequest.objects.select_related(
                'citizen', 'driver__user', 'driver__vehicle'
            ).get(id=order_id)

            driver_data = DriverSimpleSerializer(order.driver).data if order.driver else None

            # اگر کاربر لاگین نبود اما در محیط توسعه نقش راننده اعلام شد
            if not user or not user.is_authenticated:
                is_driver = (role_param == 'driver')
                return {
                    "valid": True,
                    "is_driver": is_driver,
                    "driver_id": order.driver_id if is_driver else None,
                    "status": order.status,
                    "driver": driver_data,
                }

            is_driver = bool(order.driver and order.driver.user_id == user.id)
            is_citizen = bool(order.citizen_id == user.id)

            if user.is_staff or is_driver or is_citizen:
                return {
                    "valid": True,
                    "is_driver": is_driver,
                    "driver_id": order.driver.id if order.driver else None,
                    "status": order.status,
                    "driver": driver_data,
                }

            return {"valid": False, "error": "عدم دسترسی به این سفارش"}
        except WasteRequest.DoesNotExist:
            return {"valid": False, "error": "سفارش مورد نظر وجود ندارد"}


class DriverAvailableOrdersConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_group_name = "available_waste_orders"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        await self.send_json({
            "type": "connection_established",
            "message": "اتصال به روم کاردکس زنده رانندگان برقرار شد."
        })

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def new_order_event(self, event):
        await self.send_json({"type": "new_order", "order": event["order"]})

    async def remove_order_event(self, event):
        await self.send_json({"type": "remove_order", "order_id": event["order_id"]})