# waste/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    WasteCategoryViewSet,
    CitizenWasteRequestViewSet,
    DriverWasteRequestViewSet,
)

router = DefaultRouter()
router.register(r'categories', WasteCategoryViewSet, basename='waste-categories')
router.register(r'citizen/requests', CitizenWasteRequestViewSet, basename='citizen-requests')
router.register(r'driver/requests', DriverWasteRequestViewSet, basename='driver-requests')

urlpatterns = [
    path('api/', include(router.urls)),
]