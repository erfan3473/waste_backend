# core/asgi.py

import os
from django.core.asgi import get_asgi_application

# ۱. مقداردهی تنظیمات جنگو حتماً در خط اول
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django_asgi_app = get_asgi_application()

# ۲. ایمپورت روتینگ بعد از لود اپلیکیشن جنگو
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import waste.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            waste.routing.websocket_urlpatterns
        )
    ),
})