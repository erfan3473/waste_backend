# waste/views.py

from django.db import transaction
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point

from rest_framework import viewsets, permissions, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Driver,
    WasteCategory,
    WasteRequest,
    WasteRequestHistory,
)
from .serializers import (
    WasteCategorySerializer,
    WasteRequestCreateSerializer,
    WasteRequestDetailSerializer,
)
from .utils.finance_service import settle_collected_waste_request

from django.contrib.auth import get_user_model
User = get_user_model()
# ════════════════════════════════════════════════════════════
# ۱. بخش عمومی و شهروند
# ════════════════════════════════════════════════════════════

class WasteCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """لیست دسته‌بندی‌های فعال پسماند برای نمایش در اپ شهروند"""
    queryset = WasteCategory.objects.filter(is_active=True)
    serializer_class = WasteCategorySerializer
    permission_classes = [permissions.AllowAny]


class CitizenWasteRequestViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet
):
    """ثبت و پیگیری درخواست‌های پسماند توسط شهروند"""
    # تغییر به AllowAny برای تست‌های اولیه
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        # اختصاص درخواست به اولین کاربر دیتابیس (ادمین) در صورت عدم وجود سشن
        user = self.request.user if self.request.user.is_authenticated else User.objects.first()
        return WasteRequest.objects.filter(citizen=user).prefetch_related('items__category')

    def get_serializer_class(self):
        if self.action == 'create':
            return WasteRequestCreateSerializer
        return WasteRequestDetailSerializer

    def perform_create(self, serializer):
        # ذخیره خودکار کاربر لاگین یا کاربر پیش‌فرض در متد POST
        user = self.request.user if self.request.user.is_authenticated else User.objects.first()
        serializer.save(citizen=user)
# ════════════════════════════════════════════════════════════
# ۲. بخش راننده و ناوگان
# ════════════════════════════════════════════════════════════

class DriverWasteRequestViewSet(viewsets.GenericViewSet):
    """عملیات راننده: مشاهده، پذیرش مأموریت و ثبت وزن نهایی"""
    permission_classes = [permissions.IsAuthenticated]

    def get_driver(self):
        try:
            return self.request.user.waste_driver
        except (Driver.DoesNotExist, AttributeError):
            return None

    @action(detail=False, methods=['get'], url_path='available')
    def available_requests(self, request):
        """لیست درخواست‌های در انتظار پذیرش اطراف راننده"""
        driver = self.get_driver()
        if not driver:
            return Response(
                {"detail": "حساب کاربری شما به عنوان راننده ثبت نشده است."},
                status=status.HTTP_403_FORBIDDEN
            )

        lat = request.query_params.get('lat')
        lng = request.query_params.get('lng')

        qs = WasteRequest.objects.filter(status='PENDING').prefetch_related('items__category')

        if lat and lng:
            current_p = Point(float(lng), float(lat), srid=4326)
            qs = qs.annotate(distance=Distance('location', current_p)).order_by('distance')
        else:
            qs = qs.order_by('-created_at')

        serializer = WasteRequestDetailSerializer(qs[:20], many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='accept')
    def accept_request(self, request, pk=None):
        """پذیرش درخواست توسط راننده (با بررسی همزمانی اتمیک)"""
        driver = self.get_driver()
        if not driver:
            return Response(
                {"detail": "شما راننده مجاز نیستید."},
                status=status.HTTP_403_FORBIDDEN
            )

        with transaction.atomic():
            waste_req = WasteRequest.objects.select_for_update().filter(pk=pk).first()
            if not waste_req:
                return Response(
                    {"detail": "درخواست یافت نشد."},
                    status=status.HTTP_404_NOT_FOUND
                )

            if waste_req.status != 'PENDING':
                return Response(
                    {"detail": "این سفارش قبلاً توسط راننده دیگری پذیرفته شده یا لغو شده است."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            waste_req.driver = driver
            waste_req.status = 'ACCEPTED'
            waste_req.save(update_fields=['driver', 'status', 'updated_at'])

            WasteRequestHistory.objects.create(
                request=waste_req,
                from_status='PENDING',
                to_status='ACCEPTED',
                changed_by=request.user,
                note=f"پذیرش سفارش توسط {driver.user.get_full_name() or driver.user.username}"
            )

        return Response({"detail": "سفارش با موفقیت به شما اختصاص یافت.", "status": "ACCEPTED"})

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """تغییر وضعیت راننده به ON_THE_WAY یا ARRIVED"""
        driver = self.get_driver()
        new_status = request.data.get('status')

        if new_status not in ['ON_THE_WAY', 'ARRIVED']:
            return Response(
                {"detail": "وضعیت ارسالی نامعتبر است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        waste_req = WasteRequest.objects.filter(pk=pk, driver=driver).first()
        if not waste_req:
            return Response(
                {"detail": "سفارش متعلق به شما نیست."},
                status=status.HTTP_404_NOT_FOUND
            )

        prev_status = waste_req.status
        waste_req.status = new_status
        waste_req.save(update_fields=['status', 'updated_at'])

        WasteRequestHistory.objects.create(
            request=waste_req,
            from_status=prev_status,
            to_status=new_status,
            changed_by=request.user
        )

        return Response({"status": new_status})

    @action(detail=True, methods=['post'], url_path='complete')
    def complete_collection(self, request, pk=None):
        """ثبت وزن قطعی اقلام و تسویه نهایی اتمیک"""
        driver = self.get_driver()
        items_data = request.data.get('items', [])

        if not items_data:
            return Response(
                {"detail": "ثبت اقلام وزن‌شده الزامی است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        waste_req = WasteRequest.objects.filter(pk=pk, driver=driver).first()
        if not waste_req:
            return Response(
                {"detail": "سفارش نامعتبر یا متعلق به راننده دیگری است."},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            result = settle_collected_waste_request(
                waste_request=waste_req,
                driver=driver,
                items_actual_data=items_data,
                changed_by_user=request.user
            )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)