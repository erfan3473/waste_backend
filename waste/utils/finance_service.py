# waste/utils/finance_service.py

from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from waste.models import (
    Wallet,
    Transaction,
    WasteRequestHistory,
    WasteCategory,
    WasteRequestItem,
)


def settle_collected_waste_request(waste_request, driver, items_actual_data: list, changed_by_user):
    """
    تسویه قطعی درخواست پس از وزن‌کشی توسط راننده:
    ۱. ثبت یا به‌روزرسانی اقلام وزن‌شده بر اساس category_id
    ۲. محاسبه دقیق بهای اقلام بر مبنای نرخ مصوب
    ۳. واریز اتمیک وجه به کیف پول شهروند (CITIZEN_REWARD)
    ۴. واریز دستمزد راننده (DRIVER_WAGE)
    ۵. تغییر وضعیت به COLLECTED
    """
    with transaction.atomic():
        request_obj = type(waste_request).objects.select_for_update().get(id=waste_request.id)

        if request_obj.status == 'COLLECTED':
            raise ValidationError("این درخواست پیش‌تر تسویه و نهایی شده است.")

        # ۱. استخراج وزن‌های ارسالی راننده با پشتیبانی از category_id و item_id
        cat_weights = {}
        for item in items_actual_data:
            cat_id = item.get('category_id') or item.get('item_id')
            actual_val = item.get('actual_amount')
            if cat_id is not None and actual_val is not None:
                try:
                    weight = Decimal(str(actual_val))
                    if weight > 0:
                        cat_weights[int(cat_id)] = weight
                except Exception:
                    continue

        if not cat_weights:
            raise ValidationError("هیچ وزن معتبری برای اقلام پسماند ثبت نشده است.")

        # دریافت دسته‌بندی‌ها از دیتابیس جهت محاسبه نرخ رسمی
        categories = WasteCategory.objects.filter(id__in=cat_weights.keys())
        categories_map = {c.id: c for c in categories}

        total_citizen_amount = Decimal('0')

        # واکشی اقلام از پیش ثبت‌شده (در صورت وجود)
        existing_items = {
            item.category_id: item
            for item in request_obj.items.select_for_update().all()
        }

        for cat_id, weight in cat_weights.items():
            cat = categories_map.get(cat_id)
            if not cat:
                continue

            item_cost = weight * cat.price_per_unit
            total_citizen_amount += item_cost

            if cat_id in existing_items:
                req_item = existing_items[cat_id]
                req_item.actual_amount = weight
                req_item.unit_price = cat.price_per_unit
                req_item.save(update_fields=['actual_amount', 'unit_price'])
            else:
                WasteRequestItem.objects.create(
                    request=request_obj,
                    category=cat,
                    estimated_amount=Decimal('0'),
                    actual_amount=weight,
                    unit_price=cat.price_per_unit,
                )

        # ۲. تسویه و شارژ کیف پول شهروند
        citizen_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            user=request_obj.citizen,
            defaults={'user_type': 'CITIZEN', 'balance': Decimal('0')}
        )
        citizen_wallet.balance += total_citizen_amount
        citizen_wallet.save(update_fields=['balance'])

        Transaction.objects.create(
            wallet=citizen_wallet,
            waste_request=request_obj,
            transaction_type='CITIZEN_REWARD',
            amount=total_citizen_amount,
            balance_after=citizen_wallet.balance,
            status='SUCCESS',
            description=f"واریز بهای پسماند تحویلی - درخواست #{request_obj.id}"
        )

        # ۳. واریز دستمزد راننده (۲۰,۰۰۰ تومان پایه + ۵٪ ارزش بار)
        driver_wage_amount = Decimal('20000') + (total_citizen_amount * Decimal('0.05'))
        driver_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            user=driver.user,
            defaults={'user_type': 'DRIVER', 'balance': Decimal('0')}
        )
        driver_wallet.balance += driver_wage_amount
        driver_wallet.save(update_fields=['balance'])

        Transaction.objects.create(
            wallet=driver_wallet,
            waste_request=request_obj,
            transaction_type='DRIVER_WAGE',
            amount=driver_wage_amount,
            balance_after=driver_wallet.balance,
            status='SUCCESS',
            description=f"واریز دستمزد جمع‌آوری و حمل پسماند - درخواست #{request_obj.id}"
        )

        # ۴. تغییر وضعیت سفارش و ثبت لاگ تاریخچه
        prev_status = request_obj.status
        request_obj.status = 'COLLECTED'
        request_obj.total_price = total_citizen_amount
        request_obj.save(update_fields=['status', 'total_price', 'updated_at'])

        WasteRequestHistory.objects.create(
            request=request_obj,
            from_status=prev_status,
            to_status='COLLECTED',
            changed_by=changed_by_user,
            note=f"تخلیه بار انجام شد. مبلغ شهروند: {int(total_citizen_amount):,} تومان | دستمزد راننده: {int(driver_wage_amount):,} تومان"
        )

        return {
            "total_citizen_amount": int(total_citizen_amount),
            "driver_wage_amount": int(driver_wage_amount),
            "status": "COLLECTED"
        }