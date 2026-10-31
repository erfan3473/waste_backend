# waste/routing.py

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/orders/(?P<order_id>\d+)/track/?$', consumers.OrderTrackingConsumer.as_asgi()),
    re_path(r'^ws/driver/available-orders/?$', consumers.DriverAvailableOrdersConsumer.as_asgi()),
]