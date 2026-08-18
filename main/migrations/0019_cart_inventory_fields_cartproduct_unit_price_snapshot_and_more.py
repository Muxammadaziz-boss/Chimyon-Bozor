from decimal import Decimal

from django.db import migrations, models


def backfill_cartproduct_unit_price_snapshot(apps, schema_editor):
    CartProduct = apps.get_model('main', 'CartProduct')
    for item in CartProduct.objects.select_related('product').all().iterator():
        if item.unit_price_snapshot is not None:
            continue
        if item.product_id and item.product:
            if item.product.discount_status and item.product.discount_price is not None:
                item.unit_price_snapshot = item.product.discount_price
            else:
                item.unit_price_snapshot = item.product.price
        else:
            item.unit_price_snapshot = Decimal('0.00')
        item.save(update_fields=['unit_price_snapshot'])


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0018_alter_review_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='cart',
            name='inventory_status',
            field=models.CharField(choices=[('available', 'Rezervatsiz'), ('reserved', 'Rezerv qilingan'), ('released', "Bo'shatilgan")], db_index=True, default='available', max_length=20),
        ),
        migrations.AddField(
            model_name='cart',
            name='inventory_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cartproduct',
            name='unit_price_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.RunPython(backfill_cartproduct_unit_price_snapshot, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=['pending', 'initiated']),
                fields=('order', 'provider', 'purpose', 'amount'),
                name='uniq_active_payment_per_order_provider_purpose_amount',
            ),
        ),
    ]
