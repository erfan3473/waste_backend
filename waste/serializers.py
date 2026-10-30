# waste/serializers.py

from rest_framework import serializers
from django.contrib.gis.geos import Point
from .models import WasteCategory, WasteRequest, WasteRequestItem
from django.contrib.auth import get_user_model
User = get_user_model()

class WasteCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = WasteCategory
        fields = ['id', 'title', 'unit', 'price_per_unit', 'display_order']


class WasteRequestItemInputSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    estimated_amount = serializers.DecimalField(max_digits=8, decimal_places=2)


class WasteRequestItemOutputSerializer(serializers.ModelSerializer):
    category_title = serializers.CharField(source='category.title', read_only=True)
    unit = serializers.CharField(source='category.unit', read_only=True)

    class Meta:
        model = WasteRequestItem
        fields = ['id', 'category', 'category_title', 'unit', 'estimated_amount', 'actual_amount', 'unit_price']

class WasteRequestCreateSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(write_only=True)
    longitude = serializers.FloatField(write_only=True)
    items = WasteRequestItemInputSerializer(many=True, write_only=True)

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
        items_data = validated_data.pop('items')
        lat = validated_data.pop('latitude')
        lng = validated_data.pop('longitude')

        # دریافت شهروند از آرگومان‌های ارسالی یا کاربر لاگین/اولین کاربر دیتابیس
        citizen = validated_data.pop('citizen', None)
        if not citizen:
            request = self.context.get('request')
            if request and hasattr(request, 'user') and request.user.is_authenticated:
                citizen = request.user
            else:
                citizen = User.objects.first()

        # تولید PointField سازگار با PostGIS
        point = Point(lng, lat, srid=4326)

        # تعیین وضعیت اولیه
        initial_status = 'SCHEDULED' if validated_data.get('scheduled_date') else 'PENDING'

        waste_request = WasteRequest.objects.create(
            citizen=citizen,
            location=point,
            status=initial_status,
            **validated_data
        )

        # ایجاد اقلام سفارش و ثبت نرخ لحظه‌ای
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
                        estimated_amount=item['estimated_amount'],
                        unit_price=cat.price_per_unit
                    )
                )

        WasteRequestItem.objects.bulk_create(items_to_create)
        return waste_request


class WasteRequestDetailSerializer(serializers.ModelSerializer):
    items = WasteRequestItemOutputSerializer(many=True, read_only=True)
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()

    class Meta:
        model = WasteRequest
        fields = [
            'id',
            'status',
            'latitude',
            'longitude',
            'address_text',
            'plaque',
            'unit',
            'scheduled_date',
            'scheduled_time_slot',
            'total_price',
            'items',
            'created_at',
        ]

    def get_latitude(self, obj):
        return obj.location.y if obj.location else None

    def get_longitude(self, obj):
        return obj.location.x if obj.location else None