from typing import TypeVar

from django.contrib import admin
from django.db import models
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from django_stripe_hooks.models import (
  Product, Price, PriceTier,
  Coupon, PromotionCode,
  Customer, PaymentMethod, Subscription,
  PaymentIntent, BalanceTransaction, Invoice, Charge, Refund
)


ParentModelT = TypeVar("ParentModelT", bound=models.Model)
ChildModelT = TypeVar("ChildModelT", bound=models.Model)


class StripeModelAdmin(admin.ModelAdmin[ParentModelT]):
  """ModelAdmin for Stripe models.

  Notes
  -----
  Authors are required to use Stripe SDK to create objects,
  so we remove add/change/delete permissions here.

  """

  def has_add_permission(
    self,
    request: HttpRequest,
  ) -> bool:
    return False

  def has_change_permission(
    self,
    request: HttpRequest,
    obj: ParentModelT | None = None,
  ) -> bool:
    return False

  def has_delete_permission(
    self,
    request: HttpRequest,
    obj: ParentModelT | None = None,
  ) -> bool:
    return False


class StripeModelInline(admin.TabularInline[ChildModelT, ParentModelT]):
  """Inline ModelAdmin for Stripe models.

  Notes
  -----
  Authors are required to use Stripe SDK to create objects,
  so we remove add/change/delete permissions here.

  """

  def has_add_permission(
    self,
    request: HttpRequest,
    obj: models.Model | None = None,
  ) -> bool:
    return False

  def has_change_permission(
    self,
    request: HttpRequest,
    obj: models.Model | None = None,
  ) -> bool:
    return False

  def has_delete_permission(
    self,
    request: HttpRequest,
    obj: models.Model | None = None,
  ) -> bool:
    return False


@admin.register(Product)
class ProductAdmin(StripeModelAdmin[Product]):
  list_display = (
    'name',
  )

  fieldsets = (
    (None, {
      'fields': (
        'name',
        'description',
      ),
    }),
  )


class PriceTierInline(StripeModelInline[PriceTier, Price]):
  model = PriceTier
  extra = 2
  fields = ('flat_amount', 'unit_amount', 'up_to')


# TODO: This uses custom admin html and css. Is it needed?
@admin.register(Price)
class PriceAdmin(StripeModelAdmin[Price]):
  list_display = (
    'nickname',
    'product',
    'type',
    'interval',
    'unit_amount',
    'active',
  )
  list_select_related = ('product', )

  fieldsets = (
    (None, {
      'fields': (
        'product',
        'nickname',
        'active',
      ),
    }),
    (_("Billing Details"), {
      'fields': (
        'unit_amount',
        'type',
        'interval',
        'usage_type',
        'billing_scheme',
        'tiers_mode',
      ),
    }),
  )


@admin.register(Coupon)
class CouponAdmin(StripeModelAdmin[Coupon]):
  list_display = (
    'name',
    'terms',
  )

  fieldsets = (
    (None, {
      'fields': (
        'name',
        'duration',
        'percent_off',
        'amount_off',
        'products',
      ),
    }),
  )
  filter_horizontal = ('products', )


@admin.register(PromotionCode)
class PromotionCodeAdmin(StripeModelAdmin[PromotionCode]):
  list_display = (
    'code',
    'coupon',
    'coupon__terms',
    'expires_at',
    'redemptions',
    'active',
  )
  list_select_related = ('coupon', )
  list_display_links = None

  fieldsets = (
    (None, {
      'fields': (
        'code',
        'coupon',
        'expires_at',
        'max_redemptions',
        'active',
      ),
    }),
  )

  @admin.display(description=_("Terms"))
  def coupon__terms(self, obj: PromotionCode) -> str:
    return obj.coupon.terms


class SubscriptionInline(StripeModelInline[Subscription, Customer]):
  model = Subscription
  extra = 0
  fields = (
    'status',
    'current_period_start',
    'current_period_end',
    'discounts',
    'cancel_at_period_end',
  )


class PaymentMethodInline(StripeModelInline[PaymentMethod, Customer]):
  model = PaymentMethod
  extra = 0
  fields = (
    'card_info',
    'card_exp_month',
    'card_exp_year',
    'zip_code',
    'is_default',
  )


class InvoiceInline(StripeModelInline[Invoice, Customer]):
  model = Invoice
  extra = 0
  fields = (
    'total',
    'status',
    'period_start',
    'period_end',
    'pdf',
    'link',
  )

  @admin.display(description=_("PDF"))
  def pdf_link(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice_pdf,
    )
    return html

  @admin.display(description=_("Link"))
  def link(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">Link</a>',
      url=obj.hosted_invoice_url,
    )
    return html


@admin.register(Customer)
class CustomerAdmin(StripeModelAdmin[Customer]):
  search_fields = (
    'email',
    'name',
    'phone',
  )

  list_display = (
    'email',
    'name',
    'phone',
  )

  fieldsets = (
    (None, {'fields': ()}),
  )

  inline_type = 'stacked'
  inlines = (
    SubscriptionInline,
    PaymentMethodInline,
    InvoiceInline,
  )


@admin.register(Subscription)
class SubscriptionAdmin(StripeModelAdmin[Subscription]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )
  list_display = (
    'customer',
    'status_verbose',
    'current_period_end',
  )
  list_select_related = (
    'customer',
  )

  fieldsets = (
    (None, {
      'fields': (
        'customer',
        'current_period_start',
        'current_period_end',
        'cancel_at_period_end',
        'discounts',
      ),
    }),
  )
  inlines = (InvoiceInline, )

  @admin.display(description=_("Status"), ordering='status')
  def status_verbose(self, obj: Subscription) -> str:
    if obj.cancel_at_period_end:
      status = 'cancels'
    else:
      status = obj.status

    text = {
      k: str(v) for k, v in Subscription.STATUSES
    } | {
      'active': f'Renews {obj.current_period_end:%b %d, %Y}',
      'cancels': f'Cancels {obj.current_period_end:%b %d, %Y}',
    }
    return text[status]


@admin.register(PaymentIntent)
class PaymentIntentAdmin(StripeModelAdmin[PaymentIntent]):
  search_fields = (
    'amount',
    'description',
    'customer__email',
    'customer__name',
    'customer__phone',
  )
  list_display = (
    'payment_method',
    'amount',
    'customer',
    'description',
    'status',
  )


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(StripeModelAdmin[BalanceTransaction]):
  search_fields = (
    'type',
    'status',
  )

  list_display = (
    'type',
    'status',
    'amount_display',
    'fee_display',
    'net_display',
    'available_on',
  )
  list_display_links = None

  def _currency_display(self, obj: BalanceTransaction, field: str) -> str:
    """Displays currency-related fields."""
    amount = getattr(obj, field)
    if field == 'fee' or amount < 0:
      sign = '-'
    elif amount > 0:
      sign = '+'
    elif amount == 0:
      sign = ''

    if obj.currency == 'usd':
      s = f"{sign}${abs(amount)} USD"
    else:
      s = f"{sign} {abs(amount) * 100} {obj.currency.upper()}"
    return s

  @admin.display(description=_("Amount"), ordering='amount')
  def amount_display(self, obj: BalanceTransaction) -> str:
    return self._currency_display(obj, 'amount')

  @admin.display(description=_("Fee"), ordering='fee')
  def fee_display(self, obj: BalanceTransaction) -> str:
    return self._currency_display(obj, 'fee')

  @admin.display(description=_("Net"), ordering='net')
  def net_display(self, obj: BalanceTransaction) -> str:
    return self._currency_display(obj, 'net')


@admin.register(Invoice)
class InvoiceAdmin(StripeModelAdmin[Invoice]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'customer',
    'total',
    'period_start',
    'period_end',
    'status_chip',
    'pdf',
    'link',
  )
  list_select_related = (
    'customer',
    'subscription',
  )
  list_filter = (
    'period_start',
  )
  list_display_links = None

  @admin.display(description=_("Status"), ordering='status')
  def status_chip(self, obj: Invoice) -> str:
    """Format to add badge HTML."""
    STATUSES = {
      'draft': ('Draft', 'info'),
      'open': ('Open', 'warn'),
      'paid': ('Paid', 'ok'),
      'uncollectible': ('Uncollectible', 'bad'),
      'void': ('Void', 'bad'),
    }
    status, color = STATUSES[obj.status]
    html = f'<chip class="{color}">{status}</chip>'
    return mark_safe(html)

  @admin.display(description=_("PDF"))
  def pdf(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice_pdf,
    )
    return html

  @admin.display(description=_("Link"))
  def link(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">Link</a>',
      url=obj.hosted_invoice_url,
    )
    return html


@admin.register(Charge)
class ChargeAdmin(StripeModelAdmin[Charge]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'customer',
    'amount',
    'status',
    'created',
    'description',
    'disputed',
    'refunded',
  )
  list_select_related = (
    'customer',
  )


@admin.register(Refund)
class RefundAdmin(StripeModelAdmin[Refund]):
  list_display = (
    'charge__customer',
    'amount',
    'reason',
    'status',
  )

  fieldsets = (
    (None, {
      'fields': (
        'charge',
        'reason',
        ('amount', 'currency'),
      ),
    }),
  )

  @admin.display(description=_("Customer"))
  def charge__customer(self, obj: Refund) -> str:
    assert isinstance(obj.charge, Charge)
    return str(obj.charge.customer)
