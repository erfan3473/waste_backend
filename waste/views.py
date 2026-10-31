# waste/views.py

import logging
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import authenticate, get_user_model
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.db import transaction
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Driver,
    Vehicle,
    WasteCategory,
    WasteRequest,
    WasteRequestHistory,
    Wallet,
    Transaction as WalletTransaction,
    WithdrawalRequest,
)
from .serializers import (
    CitizenSendOTPSerializer,
    CitizenVerifyOTPSerializer,
    DriverSimpleSerializer,
    TransactionSerializer,
    WalletSerializer,
    WasteCategorySerializer,
    WasteRequestCreateSerializer,
    WasteRequestDetailSerializer,
    WasteRequestHistorySerializer,
    WithdrawalRequestSerializer,
)
from .utils.finance_service import settle_collected_waste_request
from .utils.otp_service import (
    check_rate_limit,
    generate_otp_code,
    get_remaining_time,
    save_otp,
    verify_otp,
)
from .utils.sms_service import send_otp_sms_async

User = get_user_model()
logger = logging.getLogger(__name__)


def broadcast_available_order_created(order_instance):
    """ارسال ایمن دیتای سفارش جدید به روم وب‌سوکت رانندگان"""
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            data = WasteRequestDetailSerializer(order_instance).data
            async_to_sync(channel_layer.group_send)(
                "available_waste_orders",
                {
                    "type": "new_order_event",
                    "order": data
                }
            )
    except Exception as e:
        logger.error(f"خطا در ارسال پیام وب‌سوکت سفارش جدید: {e}")


def broadcast_available_order_removed(order_id):
    """ارسال ایمن دستور حذف سفارش به روم رانندگان"""
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                "available_waste_orders",
                {
                    "type": "remove_order_event",
                    "order_id": order_id
                }
            )
    except Exception as e:
        logger.error(f"خطا در ارسال پیام وب‌سوکت حذف سفارش: {e}")


# ════════════════════════════════════════════════════════════
# ۱. احراز هویت پیامکی شهروندان (OTP)
# ════════════════════════════════════════════════════════════

class CitizenSendOTPView(APIView):
    """ارسال کد ورود یکبار مصرف پیامکی به شماره شهروند"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = CitizenSendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        # بررسی وجود کد معتبر جاری در ردیس (جلوگیری از اسپم زیر ۲ دقیقه)
        remaining_ttl = get_remaining_time(phone)
        if remaining_ttl > 0:
            return Response(
                {
                    "detail": "کد تایید قبلاً برای این شماره ارسال شده است. لطفاً تا پایان زمان منتظر بمانید.",
                    "remaining_seconds": remaining_ttl
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # کنترل سقف مجاز درخواست (حداکثر ۵ تلاش در ساعت)
        allowed, remaining_attempts, wait_sec = check_rate_limit(phone)
        if not allowed:
            return Response(
                {
                    "detail": f"تعداد درخواست‌های مجاز شما به پایان رسید. لطفاً {wait_sec // 60} دقیقه دیگر تلاش فرمایید.",
                    "wait_seconds": wait_sec
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        otp_code = generate_otp_code(5)
        saved = save_otp(phone, otp_code, expire_seconds=120)
        if not saved:
            return Response(
                {"detail": "خطای داخلی سرور در برقراری ارتباط با ردیس. مجدداً تلاش کنید."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # ارسال پیامک ناهمگام با پترن 572362
        send_otp_sms_async(phone, otp_code)

        return Response({
            "message": "کد تایید ورود با موفقیت ارسال شد.",
            "phone": phone,
            "expires_in": 120
        }, status=status.HTTP_200_OK)


class CitizenVerifyOTPView(APIView):
    """تایید کد پیامک، ثبت‌نام یا لاگین شهروند و صدور توکن ماندگار DRF"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = CitizenVerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']
        code = serializer.validated_data['code']

        if not verify_otp(phone, code):
            return Response(
                {"detail": "کد تایید وارد شده نادرست است یا زمان اعتبار آن به پایان رسیده است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            user, created = User.objects.get_or_create(username=phone)
            if created:
                user.set_unusable_password()
                user.save()

            # ایجاد خودکار رکورد کیف پول شهروند در صورت ثبت‌نام اولیه
            wallet, _ = Wallet.objects.get_or_create(
                user=user,
                defaults={'user_type': 'CITIZEN', 'balance': Decimal('0')}
            )

            # صدور یا واکشی توکن پایدار احراز هویت
            token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "token": token.key,
            "user_id": user.id,
            "phone": user.username,
            "full_name": user.get_full_name() or user.username,
            "wallet_balance": int(wallet.balance),
            "is_new_user": created,
        }, status=status.HTTP_200_OK)


class CitizenLogoutView(APIView):
    """ابطال توکن احراز هویت و خروج کامل کاربر از سیستم"""
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            request.user.auth_token.delete()
        except Exception:
            pass
        return Response({"detail": "خروج با موفقیت انجام شد."}, status=status.HTTP_200_OK)


# ════════════════════════════════════════════════════════════
# ۲. بخش عمومی و ثبت درخواست‌های شهروند
# ════════════════════════════════════════════════════════════

class WasteCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """لیست نرخ‌های رسمی و مصوب پسماند خشک برای نمایش در اپ شهروند و راننده"""
    queryset = WasteCategory.objects.filter(is_active=True).order_by('display_order', 'id')
    serializer_class = WasteCategorySerializer
    permission_classes = [permissions.AllowAny]


class CitizenWasteRequestViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet
):
    """ثبت، پیگیری و تاریخچه سفارش‌های پسماند شهروند لاگین‌شده"""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return WasteRequest.objects.filter(
            citizen=self.request.user
        ).select_related(
            'driver__user', 'driver__vehicle'
        ).prefetch_related('items__category')

    def get_serializer_class(self):
        if self.action == 'create':
            return WasteRequestCreateSerializer
        return WasteRequestDetailSerializer

    def perform_create(self, serializer):
        instance = serializer.save(citizen=self.request.user)
        # برادکست بلادرنگ به رانندگان
        broadcast_available_order_created(instance)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel_request(self, request, pk=None):
        """لغو درخواست توسط شهروند قبل از پذیرش راننده"""
        waste_req = self.get_object()
        if waste_req.status not in ['PENDING', 'SCHEDULED']:
            return Response(
                {"detail": "این سفارش در وضعیت جاری قابل لغو شدن نیست."},
                status=status.HTTP_400_BAD_REQUEST
            )

        prev_status = waste_req.status
        waste_req.status = 'CANCELLED'
        waste_req.save(update_fields=['status', 'updated_at'])

        broadcast_available_order_removed(waste_req.id)

        WasteRequestHistory.objects.create(
            request=waste_req,
            from_status=prev_status,
            to_status='CANCELLED',
            changed_by=request.user,
            note="درخواست توسط شهروند لغو شد."
        )

        return Response({"status": "CANCELLED", "detail": "سفارش با موفقیت لغو شد."})


# ════════════════════════════════════════════════════════════
# ۳. احراز هویت و عملیات رانندگان ناوگان
# ════════════════════════════════════════════════════════════

class DriverLoginView(APIView):
    """ورود سازمانی رانندگان ناوگان با تلفن و رمز عبور"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone = request.data.get('phone', '').strip()
        password = request.data.get('password', '').strip()

        if not phone or not password:
            return Response(
                {"detail": "لطفاً شماره تلفن و رمز عبور را وارد کنید."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(username=phone, password=password)
        if not user:
            return Response(
                {"detail": "شماره تلفن یا کلمه عبور اشتباه است."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not hasattr(user, 'waste_driver'):
            return Response(
                {"detail": "حساب کاربری وارد شده در ناوگان رانندگان ثبت نشده است."},
                status=status.HTTP_403_FORBIDDEN
            )

        driver = user.waste_driver
        if not driver.is_approved:
            return Response(
                {"detail": "حساب رانندگی شما هنوز توسط مدیر سیستم تأیید نشده است."},
                status=status.HTTP_403_FORBIDDEN
            )

        driver.status = 'ONLINE'
        driver.save(update_fields=['status'])

        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "token": token.key,
            "driver_id": driver.id,
            "full_name": user.get_full_name() or user.username,
            "phone": user.username,
            "status": driver.status
        }, status=status.HTTP_200_OK)


class DriverWasteRequestViewSet(viewsets.GenericViewSet):
    """عملیات راننده: مشاهده کاردکس سفارش‌ها، پذیرش، تغییر وضعیت و توزین نهایی"""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_driver(self):
        if self.request.user.is_authenticated:
            try:
                return self.request.user.waste_driver
            except (Driver.DoesNotExist, AttributeError):
                pass
        return None

    @action(detail=False, methods=['get'], url_path='available')
    def available_requests(self, request):
        """لیست سفارش‌های در انتظار پذیرش (آنی و رزرو شده) اطراف راننده"""
        driver = self.get_driver()
        if not driver:
            return Response(
                {"detail": "حساب کاربری شما به عنوان راننده ثبت نشده است."},
                status=status.HTTP_403_FORBIDDEN
            )

        lat = request.query_params.get('lat')
        lng = request.query_params.get('lng')

        qs = WasteRequest.objects.filter(
            status__in=['PENDING', 'SCHEDULED'],
            driver__isnull=True
        ).select_related('citizen').prefetch_related('items__category')

        if lat and lng:
            try:
                current_p = Point(float(lng), float(lat), srid=4326)
                qs = qs.annotate(distance=Distance('location', current_p)).order_by('distance')
            except Exception:
                qs = qs.order_by('-created_at')
        else:
            qs = qs.order_by('-created_at')

        serializer = WasteRequestDetailSerializer(qs[:30], many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='current-mission')
    def current_mission(self, request):
        """دریافت مأموریت فعال فعلی راننده در صورت باز شدن مجدد اپلیکیشن"""
        driver = self.get_driver()
        if not driver:
            return Response({"detail": "راننده یافت نشد."}, status=status.HTTP_403_FORBIDDEN)

        active_req = WasteRequest.objects.filter(
            driver=driver,
            status__in=['ACCEPTED', 'ON_THE_WAY', 'ARRIVED']
        ).select_related('citizen').prefetch_related('items__category').first()

        if not active_req:
            return Response(None, status=status.HTTP_200_OK)

        serializer = WasteRequestDetailSerializer(active_req)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='accept')
    def accept_request(self, request, pk=None):
        """پذیرش اتمیک سفارش توسط راننده"""
        driver = self.get_driver()
        if not driver:
            return Response(
                {"detail": "شما راننده مجاز نیستید."},
                status=status.HTTP_403_FORBIDDEN
            )

        acting_user = request.user

        with transaction.atomic():
            waste_req = WasteRequest.objects.select_for_update().filter(pk=pk).first()
            if not waste_req:
                return Response(
                    {"detail": "درخواست یافت نشد."},
                    status=status.HTTP_404_NOT_FOUND
                )

            if waste_req.status not in ['PENDING', 'SCHEDULED'] or waste_req.driver is not None:
                return Response(
                    {"detail": "این سفارش قبلاً توسط راننده دیگری پذیرفته شده یا لغو شده است."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            prev_status = waste_req.status
            waste_req.driver = driver
            waste_req.status = 'ACCEPTED'
            waste_req.save(update_fields=['driver', 'status', 'updated_at'])

            driver.status = 'BUSY'
            driver.save(update_fields=['status'])

            WasteRequestHistory.objects.create(
                request=waste_req,
                from_status=prev_status,
                to_status='ACCEPTED',
                changed_by=acting_user,
                note=f"پذیرش مأموریت توسط {driver.user.get_full_name() or driver.user.username}"
            )

        # فراخوانی برادکست پس از Commit قطعی دیتابیس
        broadcast_available_order_removed(waste_req.id)
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"order_tracking_{waste_req.id}",
                    {
                        "type": "order_driver_assigned",
                        "status": "ACCEPTED",
                        "driver": DriverSimpleSerializer(driver).data
                    }
                )
        except Exception as e:
            logger.error(f"خطا در ارسال رویداد تخصیص راننده به اتاق رهگیری: {e}")

        return Response({"detail": "سفارش با موفقیت به شما اختصاص یافت.", "status": "ACCEPTED"})

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """تغییر وضعیت راننده به ON_THE_WAY یا ARRIVED"""
        driver = self.get_driver()
        if not driver:
            return Response({"detail": "شما راننده مجاز نیستید."}, status=status.HTTP_403_FORBIDDEN)

        new_status = request.data.get('status')
        if new_status not in ['ON_THE_WAY', 'ARRIVED']:
            return Response(
                {"detail": "وضعیت ارسالی نامعتبر است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        waste_req = WasteRequest.objects.filter(pk=pk, driver=driver).first()
        if not waste_req:
            return Response(
                {"detail": "سفارش متعلق به شما نیست یا وجود ندارد."},
                status=status.HTTP_404_NOT_FOUND
            )

        acting_user = request.user
        prev_status = waste_req.status
        waste_req.status = new_status
        waste_req.save(update_fields=['status', 'updated_at'])

        WasteRequestHistory.objects.create(
            request=waste_req,
            from_status=prev_status,
            to_status=new_status,
            changed_by=acting_user
        )
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"order_tracking_{waste_req.id}",
                    {
                        "type": "order_status_changed",
                        "status": new_status
                    }
                )
        except Exception as e:
            logger.error(f"خطا در ارسال رویداد تغییر وضعیت به اتاق رهگیری: {e}")
        return Response({"status": new_status})

    @action(detail=True, methods=['post'], url_path='complete')
    def complete_collection(self, request, pk=None):
        """توزین قطعی و تسویه حساب مالی اتمیک"""
        driver = self.get_driver()
        if not driver:
            return Response({"detail": "شما راننده مجاز نیستید."}, status=status.HTTP_403_FORBIDDEN)

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

        acting_user = request.user

        try:
            result = settle_collected_waste_request(
                waste_request=waste_req,
                driver=driver,
                items_actual_data=items_data,
                changed_by_user=acting_user
            )

            driver.status = 'ONLINE'
            driver.save(update_fields=['status'])

            # ارسال رویداد اتمام مأموریت و تسویه به وب‌سوکت رهگیری شهروند
            try:
                channel_layer = get_channel_layer()
                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        f"order_tracking_{waste_req.id}",
                        {
                            "type": "order_status_changed",
                            "status": "COLLECTED"
                        }
                    )
            except Exception as e:
                logger.error(f"خطا در ارسال وضعیت تسویه به وب‌سوکت: {e}")

            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ════════════════════════════════════════════════════════════
# ۴. بخش مالی: کیف پول، تراکنش‌ها و تسویه حساب
# ════════════════════════════════════════════════════════════

class WalletViewSet(viewsets.GenericViewSet):
    """مدیریت موجودی کیف پول و لیست تراکنش‌های کاربر لاگین‌شده"""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_wallet(self):
        user = self.request.user
        user_type = 'DRIVER' if hasattr(user, 'waste_driver') else 'CITIZEN'
        wallet, _ = Wallet.objects.get_or_create(user=user, defaults={'user_type': user_type})
        return wallet

    @action(detail=False, methods=['get'], url_path='me')
    def my_wallet(self, request):
        wallet = self.get_wallet()
        serializer = WalletSerializer(wallet)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='transactions')
    def transactions_history(self, request):
        wallet = self.get_wallet()
        qs = wallet.transactions.all().order_by('-created_at')
        serializer = TransactionSerializer(qs, many=True)
        return Response(serializer.data)


class WithdrawalRequestViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet
):
    """ثبت درخواست تسویه و واریز بانکی از موجودی کیف پول"""
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WithdrawalRequestSerializer

    def get_queryset(self):
        return WithdrawalRequest.objects.filter(wallet__user=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        user = request.user
        user_type = 'DRIVER' if hasattr(user, 'waste_driver') else 'CITIZEN'

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = Decimal(str(serializer.validated_data['amount']))

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(
                user=user,
                defaults={'user_type': user_type, 'balance': Decimal('0')}
            )

            if wallet.balance < amount:
                return Response(
                    {"detail": "موجودی کیف پول برای این درخواست برداشت کافی نیست."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # کسر موقت موجودی و ثبت تراکنش برداشت در انتظار
            wallet.balance -= amount
            wallet.save(update_fields=['balance'])

            withdrawal = serializer.save(wallet=wallet, status='PENDING')

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='WITHDRAWAL',
                amount=-amount,
                balance_after=wallet.balance,
                status='PENDING',
                description=f"ثبت درخواست تسویه بانکی به شبا {withdrawal.shaba_number}"
            )

        return Response(serializer.data, status=status.HTTP_201_CREATED)