# waste/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CitizenLogoutView,
    CitizenSendOTPView,
    CitizenVerifyOTPView,
    CitizenWasteRequestViewSet,
    DriverLoginView,
    DriverWasteRequestViewSet,
    WalletViewSet,
    WasteCategoryViewSet,
    WithdrawalRequestViewSet,
)

router = DefaultRouter()
router.register(r'categories', WasteCategoryViewSet, basename='waste-categories')
router.register(r'citizen/requests', CitizenWasteRequestViewSet, basename='citizen-requests')
router.register(r'driver/requests', DriverWasteRequestViewSet, basename='driver-requests')
router.register(r'wallet', WalletViewSet, basename='wallet')
router.register(r'withdrawals', WithdrawalRequestViewSet, basename='withdrawals')

urlpatterns = [
    # اندپوینت‌های احراز هویت پیامکی شهروند
    path('api/citizen/auth/send-otp/', CitizenSendOTPView.as_view(), name='citizen-send-otp'),
    path('api/citizen/auth/verify-otp/', CitizenVerifyOTPView.as_view(), name='citizen-verify-otp'),
    path('api/citizen/auth/logout/', CitizenLogoutView.as_view(), name='citizen-logout'),

    # اندپوینت احراز هویت سازمانی راننده
    path('api/driver/login/', DriverLoginView.as_view(), name='driver-login'),

    # مسیرهای Router ثبت‌شده
    path('api/', include(router.urls)),
]