import uuid
from decimal import Decimal
from django.contrib.auth.models import User
from django.contrib.gis.db import models as gis_models
from django.contrib.postgres.indexes import GistIndex
from django.db import models


# ════════════════════════════════════════════════════════════
# ۱. مدل‌های راننده و ناوگان
# ════════════════════════════════════════════════════════════

class Driver(models.Model):
    DRIVER_STATUS_CHOICES = [
        ('OFFLINE', 'آفلاین'),
        ('ONLINE', 'آماده به کار'),
        ('BUSY', 'در حال انجام مأموریت'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='waste_driver',
        verbose_name="کاربر سامانه"
    )
    status = models.CharField(
        max_length=10,
        choices=DRIVER_STATUS_CHOICES,
        default='OFFLINE',
        db_index=True,
        verbose_name="وضعیت کاری"
    )
    is_approved = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="تأییدیه مدارک ادمین"
    )
    current_location = gis_models.PointField(
        srid=4326,
        null=True,
        blank=True,
        verbose_name="آخرین موقعیت مکانی"
    )
    last_location_update = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان آخرین پینگ"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "راننده"
        verbose_name_plural = "رانندگان"
        indexes = [
            GistIndex(fields=['current_location'], name='driver_location_gist_idx'),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_status_display()})"


class Vehicle(models.Model):
    VEHICLE_TYPE_CHOICES = [
        ('PICKUP', 'وانت پیکان/پراید'),
        ('NISSAN', 'نیسان زامیاد'),
        ('TRUCK', 'خاور / کامیونت'),
        ('MOTOR', 'موتور سه‌چرخ'),
    ]

    driver = models.OneToOneField(
        Driver,
        on_delete=models.CASCADE,
        related_name='vehicle',
        verbose_name="راننده"
    )
    vehicle_type = models.CharField(
        max_length=20,
        choices=VEHICLE_TYPE_CHOICES,
        default='PICKUP',
        verbose_name="نوع خودرو"
    )
    plate_number = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        verbose_name="شماره پلاک"
    )
    capacity_kg = models.PositiveIntegerField(
        default=1000,
        verbose_name="ظرفیت بار (کیلوگرم)"
    )
    model_name = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="مدل خودرو"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="خودرو فعال است"
    )

    class Meta:
        verbose_name = "خودرو"
        verbose_name_plural = "ناوگان خودروها"

    def __str__(self):
        return f"{self.get_vehicle_type_display()} - پلاک: {self.plate_number}"


# ════════════════════════════════════════════════════════════
# ۲. مدل‌های درخواست جمع‌آوری و اقلام پسماند
# ════════════════════════════════════════════════════════════

class WasteCategory(models.Model):
    UNIT_CHOICES = [
        ('KG', 'کیلوگرم'),
        ('PIECE', 'تعداد / عدد'),
    ]

    title = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="عنوان پسماند (مثلاً: قرآن و ادعیه، کارتن)"
    )
    unit = models.CharField(
        max_length=10,
        choices=UNIT_CHOICES,
        default='KG',
        verbose_name="واحد سنجش"
    )
    price_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        default=Decimal('0'),
        verbose_name="نرخ خرید هر واحد (تومان)"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="فعال برای انتخاب"
    )
    display_order = models.PositiveIntegerField(
        default=0,
        verbose_name="ترتیب نمایش"
    )

    class Meta:
        verbose_name = "دسته‌بندی پسماند"
        verbose_name_plural = "دسته‌بندی‌های پسماند"
        ordering = ['display_order', 'id']

    def __str__(self):
        return f"{self.title} ({self.get_unit_display()})"


class WasteRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'در انتظار پذیرش راننده'),
        ('SCHEDULED', 'رزرو شده (زمان‌بندی آینده)'),
        ('ACCEPTED', 'پذیرفته شده توسط راننده'),
        ('ON_THE_WAY', 'راننده در مسیر مقصد'),
        ('ARRIVED', 'راننده در محل حضور دارد'),
        ('COLLECTED', 'تخلیه و تسویه نهایی شد'),
        ('CANCELLED', 'لغو شده'),
    ]

    citizen = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='waste_requests',
        verbose_name="شهروند (درخواست‌دهنده)"
    )
    driver = models.ForeignKey(
        Driver,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='handled_requests',
        verbose_name="راننده تخصیص‌یافته"
    )
    location = gis_models.PointField(
        srid=4326,
        verbose_name="موقعیت مکانی تحویل"
    )
    address_text = models.TextField(verbose_name="آدرس دقیق متنی")
    plaque = models.CharField(max_length=20, blank=True, null=True, verbose_name="پلاک")
    unit = models.CharField(max_length=20, blank=True, null=True, verbose_name="واحد")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING',
        db_index=True,
        verbose_name="وضعیت درخواست"
    )
    scheduled_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="تاریخ انتخابی برای جمع‌آوری"
    )
    scheduled_time_slot = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="بازه زمانی (مثلاً: 10 الی 12)"
    )
    total_price = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        default=Decimal('0'),
        verbose_name="مبلغ کل پرداختی به شهروند (تومان)"
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "درخواست جمع‌آوری"
        verbose_name_plural = "درخواست‌های جمع‌آوری"
        ordering = ['-created_at']
        indexes = [
            GistIndex(fields=['location'], name='request_location_gist_idx'),
            models.Index(fields=['status', 'scheduled_date'], name='req_status_date_idx'),
        ]

    def __str__(self):
        return f"درخواست #{self.id} - {self.citizen.username} ({self.get_status_display()})"


class WasteRequestItem(models.Model):
    request = models.ForeignKey(
        WasteRequest,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="درخواست مرتبط"
    )
    category = models.ForeignKey(
        WasteCategory,
        on_delete=models.PROTECT,
        verbose_name="نوع پسماند"
    )
    estimated_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="مقدار تخمینی شهروند"
    )
    actual_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="مقدار قطعی وزن‌شده راننده"
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        default=Decimal('0'),
        verbose_name="قیمت پایه هر واحد در لحظه ثبت (تومان)"
    )

    class Meta:
        verbose_name = "قلم پسماند"
        verbose_name_plural = "اقلام پسماند درخواست"

    def __str__(self):
        return f"{self.category.title} ({self.estimated_amount} {self.category.get_unit_display()})"


# ════════════════════════════════════════════════════════════
# ۳. تاریخچه گردش کار و تغییر وضعیت درخواست (Audit Trail)
# ════════════════════════════════════════════════════════════

class WasteRequestHistory(models.Model):
    request = models.ForeignKey(
        WasteRequest,
        on_delete=models.CASCADE,
        related_name='history_logs',
        verbose_name="درخواست پسماند"
    )
    from_status = models.CharField(
        max_length=20,
        choices=WasteRequest.STATUS_CHOICES,
        verbose_name="از وضعیت"
    )
    to_status = models.CharField(
        max_length=20,
        choices=WasteRequest.STATUS_CHOICES,
        verbose_name="به وضعیت"
    )
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="تغییردهنده وضعیت"
    )
    location = gis_models.PointField(
        srid=4326,
        null=True,
        blank=True,
        verbose_name="موقعیت مکانی در لحظه تغییر"
    )
    note = models.TextField(
        blank=True,
        verbose_name="توضیحات / علت لغو یا تغییر"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="زمان ثبت رویداد"
    )

    class Meta:
        verbose_name = "تاریخچه وضعیت درخواست"
        verbose_name_plural = "تاریخچه وضعیت درخواست‌ها"
        ordering = ['-created_at']

    def __str__(self):
        return f"درخواست #{self.request.id}: {self.from_status} -> {self.to_status}"


# ════════════════════════════════════════════════════════════
# ۴. سیستم مالی، کیف پول و تسویه حساب دوطرفه
# ════════════════════════════════════════════════════════════

class Wallet(models.Model):
    USER_TYPE_CHOICES = [
        ('CITIZEN', 'شهروند'),
        ('DRIVER', 'راننده'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='waste_wallet',
        verbose_name="کاربر"
    )
    user_type = models.CharField(
        max_length=10,
        choices=USER_TYPE_CHOICES,
        default='CITIZEN',
        db_index=True,
        verbose_name="نقش مالی کاربر"
    )
    balance = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        default=Decimal('0'),
        verbose_name="موجودی کیف پول (تومان)"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="کیف پول فعال است"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "کیف پول"
        verbose_name_plural = "کیف پول‌ها"

    def __str__(self):
        return f"کیف پول {self.user.username} ({self.get_user_type_display()}): {self.balance:,} تومان"


class Transaction(models.Model):
    TRANSACTION_TYPE_CHOICES = [
        ('CITIZEN_REWARD', 'واریز وجه پسماند تحویلی (شهروند)'),
        ('DRIVER_WAGE', 'واریز دستمزد / کرایه حمل (راننده)'),
        ('PLATFORM_FEE', 'کسر سهم و کارمزد سامانه'),
        ('WITHDRAWAL', 'برداشت و تسویه بانکی'),
        ('DEPOSIT', 'شارژ مستقیم کیف پول'),
        ('CORRECTION', 'تعدیل دستی مالی توسط ادمین'),
    ]
    STATUS_CHOICES = [
        ('SUCCESS', 'موفق'),
        ('PENDING', 'در انتظار تأیید'),
        ('FAILED', 'ناموفق'),
    ]

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name='transactions',
        verbose_name="کیف پول"
    )
    waste_request = models.ForeignKey(
        WasteRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
        verbose_name="درخواست پسماند مرتبط"
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPE_CHOICES,
        db_index=True,
        verbose_name="نوع تراکنش"
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        verbose_name="مبلغ تراکنش (تومان)"
    )
    balance_after = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        verbose_name="موجودی نهایی پس از تراکنش"
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='SUCCESS',
        db_index=True,
        verbose_name="وضعیت تراکنش"
    )
    tracking_code = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=uuid.uuid4,
        verbose_name="شناسه پیگیری یکتا"
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="شرح تراکنش"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "تراکنش مالی"
        verbose_name_plural = "تراکنش‌های مالی"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tracking_code} | {self.get_transaction_type_display()} | {self.amount:,} تومان"


class WithdrawalRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'در انتظار بررسی واحد مالی'),
        ('PROCESSING', 'ارسال شده به حواله پایا'),
        ('PAID', 'واریز قطعی شد'),
        ('REJECTED', 'رد شده / برگشت به کیف پول'),
    ]

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name='withdrawal_requests',
        verbose_name="کیف پول متقاضی"
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        verbose_name="مبلغ برداشت (تومان)"
    )
    shaba_number = models.CharField(
        max_length=26,
        verbose_name="شماره شبا (با فرمت IR)"
    )
    account_owner_name = models.CharField(
        max_length=150,
        verbose_name="نام صاحب حساب"
    )
    bank_name = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="نام بانک"
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default='PENDING',
        db_index=True,
        verbose_name="وضعیت درخواست تسویه"
    )
    bank_tracking_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="کد پیگیری حواله بانکی"
    )
    admin_note = models.TextField(
        blank=True,
        verbose_name="توضیحات مالی / دلیل رد"
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان پردازش بانکی"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "درخواست برداشت وجه"
        verbose_name_plural = "درخواست‌های تسویه حساب"
        ordering = ['-created_at']

    def __str__(self):
        return f"برداشت {self.wallet.user.username} - {self.amount:,} تومان ({self.get_status_display()})"