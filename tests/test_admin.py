import datetime as dt
from decimal import Decimal

from django.contrib import admin
from django.db import models
from django.http import HttpRequest
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model

from django_stripe_hooks.admin import InvoiceInline
from django_stripe_hooks.managers import allow_stripe_write
from django_stripe_hooks.models import (
  StripeModel, Product, Price, Coupon, PromotionCode,
  Customer, BalanceTransaction, Charge, Refund,
  Invoice, Subscription, SubscriptionItem,
)


User = get_user_model()

NOW = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)


class StripeAdminTest(TestCase):
  def setUp(self) -> None:
    self.admin_user = User.objects.create_superuser(
      username='admin',
      email='admin@test.com',
      password='password'
    )
    self.client.force_login(self.admin_user)

    with allow_stripe_write():
      product = Product.objects.create(
        id='prod_test',
        active=True,
        name='Test Product',
        description='A test product',
        statement_descriptor='TESTPROD',
      )
      price = Price.objects.create(
        id='price_test',
        active=True,
        nickname='Test Price',
        type='recurring',
        interval='month',
        billing_scheme='per_unit',
        tiers_mode='na',
        unit_amount=10.00,
        currency='usd',
        product=product,
      )
      coupon = Coupon.objects.create(
        id='coup_test',
        name='Test Coupon',
        currency='usd',
        percent_off=10,
        amount_off=0.00,
        duration='once',
        valid=True,
      )
      coupon_amount = Coupon.objects.create(
        id='coup_amount',
        name='Amount Coupon',
        currency='usd',
        percent_off=0,
        amount_off=Decimal('5.00'),
        duration='once',
        valid=True,
      )
      PromotionCode.objects.create(
        id='promo_test',
        active=True,
        code='TESTCODE',
        coupon=coupon,
      )
      PromotionCode.objects.create(
        id='promo_amount',
        active=True,
        code='AMOUNT10',
        max_redemptions=100,
        coupon=coupon_amount,
      )
      PromotionCode.objects.create(
        id='promo_expired',
        active=True,
        code='EXPIRED',
        expires_at=dt.date(2020, 1, 1),
        coupon=coupon,
      )
      self.customer = Customer.objects.create(
        id='cus_test',
        email='test@example.com',
        name='Test Customer',
        phone='555-1234',
        deleted=False,
      )
      balance_transaction = BalanceTransaction.objects.create(
        id='txn_test',
        amount=100.00,
        currency='usd',
        fee=2.00,
        net=0.00,  # Covers the `amount == 0` branch in _currency_display
        status='available',
        type='charge',
        available_on=NOW,
      )
      BalanceTransaction.objects.create(
        id='txn_eur',
        amount=50.00,
        currency='eur',  # Covers the non-USD branch in _currency_display
        fee=1.00,
        net=49.00,
        status='available',
        type='charge',
        available_on=NOW,
      )
      charge = Charge.objects.create(
        id='ch_test',
        amount=100.00,
        created=NOW,
        currency='usd',
        description='',
        disputed=False,
        refunded=False,
        status='succeeded',
        customer=self.customer,
        balance_transaction=balance_transaction,
        receipt_email='test@example.com',
      )
      Refund.objects.create(
        id='re_test',
        amount=50.00,
        currency='usd',
        reason='requested_by_customer',
        status='succeeded',
        charge=charge,
      )
      Invoice.objects.create(
        id='in_test',
        created=NOW,
        number='INV-001',
        auto_advance=True,
        collection_method='charge_automatically',
        status='paid',
        currency='usd',
        amount_due=100.00,
        amount_paid=100.00,
        amount_overpaid=0.00,
        amount_remaining=0.00,
        amount_shipping=0.00,
        total_taxes_amount=0.00,
        total_discounts_amount=0.00,
        subtotal_excluding_tax=100.00,
        subtotal=100.00,
        total_excluding_tax=100.00,
        total=100.00,
        period_start=NOW,
        period_end=NOW,
        invoice_pdf='https://example.com/invoice.pdf',
        hosted_invoice_url='https://example.com/invoice',
        customer=self.customer,
      )
      subscription = Subscription.objects.create(
        id='sub_test',
        status='active',
        cancel_at_period_end=False,
        customer=self.customer,
        collection_method='charge_automatically',
      )
      SubscriptionItem.objects.create(
        id='si_test',
        price=price,
        quantity=1,
        current_period_start=NOW,
        current_period_end=NOW + dt.timedelta(days=30),
        subscription=subscription,
      )
      # Second subscription with cancel_at_period_end=True covers that branch
      # in SubscriptionAdmin.status_verbose.
      canceling_sub = Subscription.objects.create(
        id='sub_canceling',
        status='active',
        cancel_at_period_end=True,
        customer=self.customer,
        collection_method='charge_automatically',
      )
      SubscriptionItem.objects.create(
        id='si_canceling',
        price=price,
        quantity=1,
        current_period_start=NOW,
        current_period_end=NOW + dt.timedelta(days=30),
        subscription=canceling_sub,
      )

  def test_changelist_views(self) -> None:
    """Iterate through all StripeModel admins to check custom properties."""

    for model in StripeModel.__subclasses__():
      if model not in admin.site._registry:
        continue

      name = f'{model._meta.app_label}_{model._meta.model_name}'
      url = reverse(f'admin:{name}_changelist')

      response = self.client.get(url)
      assert response.status_code == 200

  def test_change_views(self) -> None:
    """Hit the change view for models with inlines."""

    url = reverse(
      'admin:django_stripe_hooks_customer_change',
      args=['cus_test'],
    )
    response = self.client.get(url)
    assert response.status_code == 200

  def test_invoice_inline_display_methods(self) -> None:
    """Call InvoiceInline display methods directly."""

    invoice = Invoice.objects.get(id='in_test')
    inline = InvoiceInline(Customer, admin.site)

    pdf_html = inline.pdf_link(invoice)
    assert 'href' in pdf_html
    assert 'PDF' in pdf_html

    link_html = inline.link(invoice)
    assert 'href' in link_html
    assert 'Link' in link_html

  def test_admin_permissions(self) -> None:
    """Verify that all StripeModel admins are read-only."""

    factory = RequestFactory()
    request = factory.get('/')
    request.user = self.admin_user

    for model in StripeModel.__subclasses__():
      if model not in admin.site._registry:
        continue

      model_admin = admin.site._registry[model]
      self.assert_readonly(model_admin, request)

      for inline_class in getattr(model_admin, 'inlines', []):
        inline_instance = inline_class(model, admin.site)
        self.assert_readonly(inline_instance, request)

  def assert_readonly(
    self,
    admin_obj: admin.options.BaseModelAdmin[models.Model],
    request: HttpRequest,
  ) -> None:
    """Assertions for the three primary write permissions."""
    self.assertFalse(admin_obj.has_add_permission(request))
    self.assertFalse(admin_obj.has_change_permission(request))
    self.assertFalse(admin_obj.has_delete_permission(request))
