# waste/utils/finance_service.py

from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from waste.models import Wallet, Transaction, WasteRequestHistory


def settle_collected_waste_request(waste_request, driver, items_actual_data: list, changed_by_user):
    """
    تسویه قطعی درخواست پس از وزن‌کشی توسط راننده:
    ۱. ثبت وزن قطعی اقلام و محاسبه مجموع بهای پسماند
    ۲. قفل اتمیک کیف پول شهروند و واریز وجه پسماند (CITIZEN_REWARD)
    ۳. واریز دستمزد حمل به کیف پول راننده (DRIVER_WAGE)
    ۴. به‌روزرسانی وضعیت به COLLECTED و ثبت در تاریخچه
    """
    with transaction.atomic():
        # بازخوانی با قفل برای جلوگیری از تغییر همزمان
        request_obj = type(waste_request).objects.select_for_update().get(id=waste_request.id)

        if request_obj.status == 'COLLECTED':
            raise ValidationError("این درخواست پیش‌تر تسویه و نهایی شده است.")

        # ۱. محاسبه مبالغ بر اساس وزن قطعی
        total_citizen_amount = Decimal('0')
        items_dict = {item['item_id']: Decimal(str(item['actual_amount'])) for item in items_actual_data}

        for req_item in request_obj.items.select_for_update().all():
            if req_item.id in items_dict:
                actual = items_dict[req_item.id]
                req_item.actual_amount = actual
                req_item.save(update_fields=['actual_amount'])
                total_citizen_amount += actual * req_item.unit_price

        # ۲. تسویه کیف پول شهروند
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

        # ۳. واریز دستمزد راننده (مثال: کرایه ثابت ۲۰,۰۰۰ تومان + ۵٪ از ارزش بار)
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

        # ۴. تغییر وضعیت سفارش و ثبت تاریخچه
        prev_status = request_obj.status
        request_obj.status = 'COLLECTED'
        request_obj.total_price = total_citizen_amount
        request_obj.save(update_fields=['status', 'total_price', 'updated_at'])

        WasteRequestHistory.objects.create(
            request=request_obj,
            from_status=prev_status,
            to_status='COLLECTED',
            changed_by=changed_by_user,
            note=f"تخلیه بار انجام شد. مبلغ شهروند: {total_citizen_amount:,} تومان | دستمزد راننده: {driver_wage_amount:,} تومان"
        )

        return {
            "total_citizen_amount": total_citizen_amount,
            "driver_wage_amount": driver_wage_amount,
            "status": "COLLECTED"
        }