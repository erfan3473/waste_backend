from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
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


@admin.register(Driver)
class DriverAdmin(GISModelAdmin):
    list_display = ('id', 'user', 'status', 'is_approved', 'last_location_update')
    list_filter = ('status', 'is_approved')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('id', 'driver', 'vehicle_type', 'plate_number', 'capacity_kg', 'is_active')
    list_filter = ('vehicle_type', 'is_active')
    search_fields = ('plate_number', 'driver__user__username')


@admin.register(WasteCategory)
class WasteCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'unit', 'price_per_unit', 'is_active', 'display_order')
    list_editable = ('price_per_unit', 'is_active', 'display_order')
    search_fields = ('title',)


class WasteRequestItemInline(admin.TabularInline):
    model = WasteRequestItem
    extra = 0


@admin.register(WasteRequest)
class WasteRequestAdmin(GISModelAdmin):
    list_display = ('id', 'citizen', 'driver', 'status', 'scheduled_date', 'scheduled_time_slot', 'total_price', 'created_at')
    list_filter = ('status', 'scheduled_date')
    search_fields = ('citizen__username', 'driver__user__username', 'address_text')
    inlines = [WasteRequestItemInline]


@admin.register(WasteRequestHistory)
class WasteRequestHistoryAdmin(GISModelAdmin):
    list_display = ('id', 'request', 'from_status', 'to_status', 'changed_by', 'created_at')
    list_filter = ('from_status', 'to_status')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'user_type', 'balance', 'is_active', 'updated_at')
    list_filter = ('user_type', 'is_active')
    search_fields = ('user__username',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'transaction_type', 'amount', 'balance_after', 'status', 'tracking_code', 'created_at')
    list_filter = ('transaction_type', 'status')
    search_fields = ('tracking_code', 'wallet__user__username')


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'amount', 'shaba_number', 'account_owner_name', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('shaba_number', 'account_owner_name', 'bank_tracking_code')