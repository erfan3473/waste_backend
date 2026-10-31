# waste/serializers.py

import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import (
    Driver,
    Vehicle,
    WasteCategory,
    WasteRequest,
    WasteRequestItem,
    WasteRequestHistory,
    Wallet,
    Transaction,
    WithdrawalRequest,
)

User = get_user_model()
import re
from .utils.sms_service import normalize_phone_for_meli

# ════════════════════════════════════════════════════════════
# ۱. سریالایزرهای دسته‌بندی و اقلام پسماند
# ════════════════════════════════════════════════════════════

class WasteCategorySerializer(serializers.ModelSerializer):
    unit_display = serializers.CharField(source='get_unit_display', read_only=True)

    class Meta:
        model = WasteCategory
        fields = [
            'id',
            'title',
            'unit',
            'unit_display',
            'price_per_unit',
            'is_active',
            'display_order',
        ]


class WasteRequestItemInputSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    estimated_amount = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default=Decimal('0.00'))


class WasteRequestItemOutputSerializer(serializers.ModelSerializer):
    category_title = serializers.CharField(source='category.title', read_only=True)
    unit = serializers.CharField(source='category.unit', read_only=True)
    unit_display = serializers.CharField(source='category.get_unit_display', read_only=True)
    total_item_price = serializers.SerializerMethodField()

    class Meta:
        model = WasteRequestItem
        fields = [
            'id',
            'category',
            'category_title',
            'unit',
            'unit_display',
            'estimated_amount',
            'actual_amount',
            'unit_price',
            'total_item_price',
        ]

    def get_total_item_price(self, obj):
        amount = obj.actual_amount if obj.actual_amount is not None else obj.estimated_amount
        return int(amount * obj.unit_price)


# ════════════════════════════════════════════════════════════
# ۲. سریالایزرهای ناوگان و راننده
# ════════════════════════════════════════════════════════════

class VehicleSerializer(serializers.ModelSerializer):
    vehicle_type_display = serializers.CharField(source='get_vehicle_type_display', read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            'id',
            'vehicle_type',
            'vehicle_type_display',
            'plate_number',
            'capacity_kg',
            'model_name',
            'is_active',
        ]


class DriverSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    phone = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vehicle = VehicleSerializer(read_only=True)

    class Meta:
        model = Driver
        fields = [
            'id',
            'full_name',
            'phone',
            'status',
            'status_display',
            'is_approved',
            'vehicle',
        ]

    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


# ════════════════════════════════════════════════════════════
# ۳. سریالایزرهای درخواست جمع‌آوری (شهروند و راننده)
# ════════════════════════════════════════════════════════════

class WasteRequestCreateSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(write_only=True)
    longitude = serializers.FloatField(write_only=True)
    items = WasteRequestItemInputSerializer(many=True, write_only=True, required=False, default=list)

    class Meta:
        model = WasteRequest
        fields = [
            'id',
            'latitude',
            'longitude',
            'address_text',
            'plaque',
            'unit',
            'scheduled_date',
            'scheduled_time_slot',
            'items',
            'status',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'created_at']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        lat = validated_data.pop('latitude')
        lng = validated_data.pop('longitude')

        citizen = validated_data.pop('citizen', None)
        if not citizen:
            request = self.context.get('request')
            if request and hasattr(request, 'user') and request.user.is_authenticated:
                citizen = request.user
            else:
                citizen = User.objects.first()

        point = Point(lng, lat, srid=4326)
        initial_status = 'SCHEDULED' if validated_data.get('scheduled_date') else 'PENDING'

        waste_request = WasteRequest.objects.create(
            citizen=citizen,
            location=point,
            status=initial_status,
            **validated_data
        )

        if items_data:
            category_ids = [item['category_id'] for item in items_data]
            categories_map = {c.id: c for c in WasteCategory.objects.filter(id__in=category_ids)}

            items_to_create = []
            for item in items_data:
                cat = categories_map.get(item['category_id'])
                if cat:
                    items_to_create.append(
                        WasteRequestItem(
                            request=waste_request,
                            category=cat,
                            estimated_amount=Decimal(str(item.get('estimated_amount', 0))),
                            unit_price=cat.price_per_unit
                        )
                    )
            if items_to_create:
                WasteRequestItem.objects.bulk_create(items_to_create)

        return waste_request


class WasteRequestDetailSerializer(serializers.ModelSerializer):
    items = WasteRequestItemOutputSerializer(many=True, read_only=True)
    driver = DriverSimpleSerializer(read_only=True)
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    citizen_name = serializers.SerializerMethodField()
    citizen_phone = serializers.CharField(source='citizen.username', read_only=True)

    class Meta:
        model = WasteRequest
        fields = [
            'id',
            'status',
            'status_display',
            'latitude',
            'longitude',
            'address_text',
            'plaque',
            'unit',
            'scheduled_date',
            'scheduled_time_slot',
            'total_price',
            'citizen_name',
            'citizen_phone',
            'driver',
            'items',
            'created_at',
            'updated_at',
        ]

    def get_latitude(self, obj):
        return obj.location.y if obj.location else None

    def get_longitude(self, obj):
        return obj.location.x if obj.location else None

    def get_citizen_name(self, obj):
        return obj.citizen.get_full_name() or obj.citizen.username


class WasteRequestHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()
    from_status_display = serializers.CharField(source='get_from_status_display', read_only=True)
    to_status_display = serializers.CharField(source='get_to_status_display', read_only=True)

    class Meta:
        model = WasteRequestHistory
        fields = [
            'id',
            'from_status',
            'from_status_display',
            'to_status',
            'to_status_display',
            'changed_by_name',
            'note',
            'created_at',
        ]

    def get_changed_by_name(self, obj):
        if obj.changed_by:
            return obj.changed_by.get_full_name() or obj.changed_by.username
        return "سیستم"


# ════════════════════════════════════════════════════════════
# ۴. سریالایزرهای کیف پول، تراکنش‌ها و تسویه حساب
# ════════════════════════════════════════════════════════════

class TransactionSerializer(serializers.ModelSerializer):
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Transaction
        fields = [
            'id',
            'waste_request',
            'transaction_type',
            'transaction_type_display',
            'amount',
            'balance_after',
            'status',
            'status_display',
            'tracking_code',
            'description',
            'created_at',
        ]


class WalletSerializer(serializers.ModelSerializer):
    user_type_display = serializers.CharField(source='get_user_type_display', read_only=True)
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            'id',
            'user_type',
            'user_type_display',
            'balance',
            'is_active',
            'recent_transactions',
            'updated_at',
        ]

    def get_recent_transactions(self, obj):
        qs = obj.transactions.all()[:10]
        return TransactionSerializer(qs, many=True).data


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = WithdrawalRequest
        fields = [
            'id',
            'amount',
            'shaba_number',
            'account_owner_name',
            'bank_name',
            'status',
            'status_display',
            'bank_tracking_code',
            'admin_note',
            'processed_at',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'bank_tracking_code', 'admin_note', 'processed_at', 'created_at']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("مبلغ برداشت باید بیشتر از صفر باشد.")
        return value

    def validate_shaba_number(self, value):
        cleaned = value.strip().upper()
        if not cleaned.startswith('IR') or len(cleaned) != 26:
            raise serializers.ValidationError("شماره شبا باید با IR شروع شده و ۲۶ کاراکتر باشد.")
        return cleaned

class CitizenSendOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        phone = normalize_phone_for_meli(value)
        if not re.match(r'^09\d{9}$', phone):
            raise serializers.ValidationError("شماره موبایل نامعتبر است. فرمت صحیح: ۰۹xxxxxxxxx")
        return phone


class CitizenVerifyOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=6)

    def validate_phone(self, value):
        phone = normalize_phone_for_meli(value)
        if not re.match(r'^09\d{9}$', phone):
            raise serializers.ValidationError("شماره موبایل نامعتبر است.")
        return phone

    def validate_code(self, value):
        cleaned = value.strip()
        if not cleaned.isdigit():
            raise serializers.ValidationError("کد تایید باید فقط شامل ارقام باشد.")
        return cleaned        