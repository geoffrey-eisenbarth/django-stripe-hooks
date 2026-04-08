from typing import TypeVar, Any

from django.contrib import admin
from django.db import models
from django.http import HttpRequest
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from django_stripe_hooks.models import (
  CURRENCY_SYMBOL,
  Product, Price, PriceTier,
  Coupon, PromotionCode, Discount,
  Customer, Subscription,
  PaymentMethod, FundingInstructions,
  PaymentIntent, ConfirmationToken,
  BalanceTransaction, Invoice, InvoicePayment, Charge, Refund
)


ParentModelT = TypeVar("ParentModelT", bound=models.Model)
ChildModelT = TypeVar("ChildModelT", bound=models.Model)


def get_currency_display(
  obj: ParentModelT,
  field: str,
  show_sign: bool = False,
  currency: str = '',
) -> str:
  """Displays currency-related fields."""
  amount = getattr(obj, field)
  if show_sign:
    if field == 'fee' or amount < 0:
      sign = '-'
    elif amount > 0:
      sign = '+'
    elif amount == 0:
      sign = ''
  else:
    sign = ''

  currency = getattr(obj, 'currency', currency)
  symbol = CURRENCY_SYMBOL.get(currency, '')
  s = f"{sign}{symbol}{abs(amount)} {currency.upper()}"
  return s.strip()


def get_address_display(
  address: dict[str, Any],
) -> str:
  """Formats addresses for display."""
  city = address.get('city')
  state = address.get('state')
  postal_code = address.get('postal_code')
  if city and state and postal_code:
    address['line3'] = f"{city}, {state} {postal_code}"
  elif city and state:
    address['line3'] = f"{city}, {state} {postal_code}"
  elif postal_code:
    address['line3'] = postal_code
  else:
    address['line3'] = None

  lines = '<br>'.join(
    filter(None, [
      address.get('line1'),
      address.get('line2'),
      address.get('line3'),
      address.get('country'),
    ])
  )
  if lines:
    html = format_html(
      '<address style="font-size: 0.8em;">'
      '{lines}'
      '</address>',
      lines=mark_safe(lines),
    )
  else:
    html = mark_safe("N/A")
  return html


def get_related_link(
  obj: ParentModelT,
  related_field: str,
  display_field: str = 'id',
) -> str:
  """Helper to generate admin links for related objects."""
  if other := getattr(obj, related_field):
    html = format_html(
      '<a href="{url}">{display}</a>',
      url=reverse(
        f"admin:{other._meta.app_label}_{other._meta.model_name}_change",
        args=[other.pk],
      ),
      display=getattr(other, display_field) or other.id,
    )
  else:
    html = mark_safe("N/A")
  return html


class StripeModelAdmin(admin.ModelAdmin[ParentModelT]):
  """ModelAdmin for Stripe models.

  Notes
  -----
  Authors are required to use Stripe SDK to create objects,
  so we remove add/change/delete permissions here.

  """

  list_display_links: tuple[str] | None = None

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

  select_related: tuple[str] | None = None

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

  def get_queryset(self, request: HttpRequest) -> models.QuerySet[ChildModelT]:
    qs = super().get_queryset(request)
    if self.select_related:
      qs = qs.select_related(*self.select_related)
    return qs


@admin.register(Product)
class ProductAdmin(StripeModelAdmin[Product]):
  list_display = (
    'name',
    'description',
    'statement_descriptor',
    'active',
    'deleted',
  )


class PriceTierInline(StripeModelInline[PriceTier, Price]):
  model = PriceTier
  extra = 0
  fields = (
    'up_to',
    'flat_amount_display',
    'unit_amount_display',
    'price_link',
  )
  readonly_fields = (
    'flat_amount_display',
    'unit_amount_display',
    'price_link',
  )

  @admin.display(description=_("Flat Amount"), ordering='flat_amount')
  def flat_amount_display(self, obj: PriceTier) -> str:
    return get_currency_display(obj, 'flat_amount')

  @admin.display(description=_("Unit Amount"), ordering='unit_amount')
  def unit_amount_display(self, obj: PriceTier) -> str:
    return get_currency_display(obj, 'unit_amount')

  @admin.display(description=_("Price"), ordering='price')
  def price_link(self, obj: PriceTier) -> str:
    return get_related_link(obj, 'price', display_field='nickname')


@admin.register(Price)
class PriceAdmin(StripeModelAdmin[Price]):
  list_display_links = ('nickname', )
  list_display = (
    'nickname',
    'unit_amount_display',
    'billing_scheme',
    'recurring_display',
    'usage_type',
    'tiers_mode',
    'product_link',
    'active',
  )
  list_select_related = ('product', )

  inlines = (PriceTierInline, )

  @admin.display(description=_("Recurring"))
  def recurring_display(self, obj: Price) -> str:
    if obj.type == 'recurring':
      if obj.interval_count == 1:
        s = f"Every {obj.interval}"
      else:
        s = f"Every {obj.interval_count} {obj.interval}s"
    else:
      s = "One-time charge"
    return s

  @admin.display(description=_("Unit Price"), ordering='unit_amount')
  def unit_amount_display(self, obj: Price) -> str:
    return get_currency_display(obj, 'unit_amount')

  @admin.display(description=_("Product"), ordering='product')
  def product_link(self, obj: Price) -> str:
    return get_related_link(obj, 'product', display_field='name')


@admin.register(Coupon)
class CouponAdmin(StripeModelAdmin[Coupon]):
  list_display_links = ('id', )
  list_display = (
    'id',
    'name',
    'terms',
    'redemptions',
    'redeem_by',
    'valid',
    'deleted'
  )


@admin.register(PromotionCode)
class PromotionCodeAdmin(StripeModelAdmin[PromotionCode]):
  list_display = (
    'code',
    'redemptions',
    'expires_at',
    'coupon_link',
    'customer_link',
    'active',
  )
  list_select_related = (
    'coupon',
    'customer',
  )

  @admin.display(description=_("Terms"))
  def coupon__terms(self, obj: PromotionCode) -> str:
    return obj.coupon.terms

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: PromotionCode) -> str:
    return get_related_link(obj, 'customer')

  @admin.display(description=_("Coupon"), ordering='coupon')
  def coupon_link(self, obj: PromotionCode) -> str:
    return get_related_link(obj, 'coupon', display_field='terms')


@admin.register(Discount)
class DiscountAdmin(StripeModelAdmin[Discount]):
  list_display = (
    'start',
    'end',
    'coupon_link',
    'promotion_code_link',
    'customer_link',
    'subscription_link',
    'subscription_item_link',
    'invoice_link',
  )
  list_select_related = (
    'coupon',
    'promotion_code',
    'customer',
    'subscription',
    'subscription_item',
    'invoice',
  )

  @admin.display(description=_("Coupon"), ordering='coupon')
  def coupon_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'coupon', display_field='terms')

  @admin.display(description=_("Promotion Code"), ordering='promotion_code')
  def promotion_code_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'promotion_code', display_field='code')

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'customer')

  @admin.display(description=_("Subscription"), ordering='subscription')
  def subscription_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'subscription')

  @admin.display(description=_("Subscription Item"), ordering='subscription_item')  # noqa: E501
  def subscription_item_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'subscription_item')

  @admin.display(description=_("Invoice"), ordering='invoice')
  def invoice_link(self, obj: Discount) -> str:
    return get_related_link(obj, 'invoice', display_field='number')


class SubscriptionMixin:
  @admin.display(description=_("Status"), ordering='status')
  def status_display(self, obj: Subscription) -> str:
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

  @admin.display(description=_("Default Payment Method"), ordering='default_payment_method')  # noqa: E501
  def default_payment_method_link(self, obj: Subscription) -> str:
    return get_related_link(obj, 'default_payment_method', display_field='card_info')  # noqa: E501


class SubscriptionInline(
  SubscriptionMixin,
  StripeModelInline[Subscription, Customer]
):
  model = Subscription
  select_related = ('default_payment_method', )
  extra = 0
  fields = (
    'status_display',
    'current_period_start_display',
    'current_period_end_display',
    'collection_method',
    'default_payment_method_link',
    'cancel_at_period_end',
  )
  readonly_fields = (
    'status_display',
    'current_period_start_display',
    'current_period_end_display',
    'cancel_at_period_end',
    'default_payment_method_link',
  )

  @admin.display(description=_("Current Period Start"))
  def current_period_start_display(self, obj: Subscription) -> str:
    return date_format(obj.current_period_start, 'DATETIME_FORMAT')

  @admin.display(description=_("Current Period End"))
  def current_period_end_display(self, obj: Subscription) -> str:
    return date_format(obj.current_period_end, 'DATETIME_FORMAT')


class PaymentMethodInline(StripeModelInline[PaymentMethod, Customer]):
  model = PaymentMethod
  extra = 0
  fields = (
    'type',
    'card_info',
    'billing_details_display',
  )
  readonly_fields = ('card_info', 'billing_details_display')

  @admin.display(description=_("Billing Details"))
  def billing_details_display(self, obj: PaymentMethod) -> str:
    return get_address_display(obj.billing_details.get('address', {}))


class FundingInstructionsInline(
  StripeModelInline[FundingInstructions, Customer]
):
  model = FundingInstructions
  extra = 0
  fields = (
    'account_holder_name',
    'account_type',
    'bank_name',
    'account_number',
    'routing_number',
    'swift_code',
    'bank_address_display',
    'account_holder_address_display',
  )
  readonly_fields = (
    'account_holder_address_display',
    'bank_address_display',
  )

  @admin.display(description=_("Account Holder Address"))
  def account_holder_address_display(self, obj: FundingInstructions) -> str:
    return get_address_display(obj.account_holder_address)

  @admin.display(description=_("Bank Address"))
  def bank_address_display(self, obj: FundingInstructions) -> str:
    return get_address_display(obj.bank_address)


class InvoiceMixin:
  @admin.display(description=_("Total"), ordering='total')
  def total_display(self, obj: Invoice) -> str:
    return get_currency_display(obj, 'total')

  @admin.display(description=_("PDF"))
  def pdf(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice_pdf,
    )
    return html

  @admin.display(description=_("Portal"))
  def portal(self, obj: Invoice) -> str:
    html = format_html(
      '<a href="{url}">Link</a>',
      url=obj.hosted_invoice_url,
    )
    return html


class InvoiceInline(
  InvoiceMixin,
  StripeModelInline[Invoice, Customer | Subscription],
):
  model = Invoice
  extra = 0

  fields = (
    'number',
    'status',
    'total_display',
    'period_start',
    'period_end',
    'collection_method',
    'pdf',
    'portal',
  )
  readonly_fields = ('total_display', 'pdf', 'portal')


@admin.register(Customer)
class CustomerAdmin(StripeModelAdmin[Customer]):
  search_fields = (
    'email',
    'name',
    'phone',
  )

  list_display_links = ('id', )
  list_display = (
    'id',
    'email',
    'name',
    'phone',
    'deleted',
  )

  inline_type = 'stacked'
  inlines = (
    SubscriptionInline,
    InvoiceInline,
    PaymentMethodInline,
    FundingInstructionsInline,
  )


@admin.register(PaymentMethod)
class PaymentMethodAdmin(StripeModelAdmin[PaymentMethod]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )
  list_display_links = ('type', )
  list_display = (
    'type',
    'card_info',
    'billing_details_display',
    'customer_link',
  )
  list_select_related = (
    'customer',
  )

  @admin.display(description=_("Billing Details"))
  def billing_details_display(self, obj: PaymentMethod) -> str:
    return get_address_display(obj.billing_details.get('address', {}))

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: PaymentMethod) -> str:
    return get_related_link(obj, 'customer')


@admin.register(FundingInstructions)
class FundingInstructionsAdmin(StripeModelAdmin[FundingInstructions]):
  list_display = (
    'customer_link',
    'bank_name',
    'account_number',
    'routing_number',
    'swift_code',
    'bank_address_display',
    'account_holder_address_display',
  )

  @admin.display(description=_("Account Holder Address"))
  def account_holder_address_display(self, obj: FundingInstructions) -> str:
    return get_address_display(obj.account_holder_address)

  @admin.display(description=_("Bank Address"))
  def bank_address_display(self, obj: FundingInstructions) -> str:
    return get_address_display(obj.bank_address)

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: FundingInstructions) -> str:
    return get_related_link(obj, 'customer')


@admin.register(PaymentIntent)
class PaymentIntentAdmin(StripeModelAdmin[PaymentIntent]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
    'description',
  )
  list_display = (
    'description',
    'status',
    'amount_display',
    'setup_future_usage',
    'receipt_email',
    'customer_link',
    'payment_method_link',
  )
  list_select_related = (
    'customer',
    'payment_method',
  )

  @admin.display(description=_("Amount"), ordering='amount')
  def amount_display(self, obj: PaymentIntent) -> str:
    return get_currency_display(obj, 'amount', show_sign=True)

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: PaymentIntent) -> str:
    return get_related_link(obj, 'customer')

  @admin.display(description=_("Payment Method"), ordering='payment_method')
  def payment_method_link(self, obj: PaymentIntent) -> str:
    return get_related_link(obj, 'payment_method', display_field='card_info')


@admin.register(ConfirmationToken)
class ConfirmationTokenAdmin(StripeModelAdmin[ConfirmationToken]):
  list_display = (
    'id',
    'created',
    'expires_at',
    'card_info',
    'customer_link',
    'is_expired',
  )
  list_select_related = ('customer', )

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: ConfirmationToken) -> str:
    return get_related_link(obj, 'customer')


@admin.register(Subscription)
class SubscriptionAdmin(SubscriptionMixin, StripeModelAdmin[Subscription]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display_links = ('id', )
  list_display = (
    'id',
    'status_display',
    'current_period_start',
    'current_period_end',
    'collection_method',
    'customer_link',
    'default_payment_method_link',
    'cancel_at_period_end',
  )
  list_select_related = (
    'customer',
  )

  inlines = (InvoiceInline, )

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: Subscription) -> str:
    return get_related_link(obj, 'customer')


@admin.register(Invoice)
class InvoiceAdmin(InvoiceMixin, StripeModelAdmin[Invoice]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'number',
    'status',
    'total_display',
    'period_start',
    'period_end',
    'collection_method',
    'pdf',
    'portal',
    'customer_link',
    'subscription_link',
  )
  list_select_related = (
    'customer',
    'subscription',
  )

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: Invoice) -> str:
    return get_related_link(obj, 'customer')

  @admin.display(description=_("Subscription"), ordering='subscription')
  def subscription_link(self, obj: Invoice) -> str:
    return get_related_link(obj, 'subscription')


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(StripeModelAdmin[InvoicePayment]):
  list_display = (
    'created',
    'status',
    'amount_paid_display',
    'amount_requested_display',
    'payment_intent_link',
    'invoice_link',
    'is_default',
  )
  list_select_related = (
    'payment_intent',
    'invoice',
  )

  @admin.display(description=_("Paid"), ordering='amount_paid')
  def amount_paid_display(self, obj: InvoicePayment) -> str:
    return get_currency_display(obj, 'amount_paid')

  @admin.display(description=_("Paid"), ordering='amount_requested')
  def amount_requested_display(self, obj: InvoicePayment) -> str:
    return get_currency_display(obj, 'amount_requested')

  @admin.display(description=_("Payment Intent"), ordering='payment_intent')
  def payment_intent_link(self, obj: InvoicePayment) -> str:
    return get_related_link(obj, 'payment_intent')

  @admin.display(description=_("Invoice"), ordering='invoice')
  def invoice_link(self, obj: InvoicePayment) -> str:
    return get_related_link(obj, 'invoice', display_field='number')


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

  @admin.display(description=_("Amount"), ordering='amount')
  def amount_display(self, obj: BalanceTransaction) -> str:
    return get_currency_display(obj, 'amount', show_sign=True)

  @admin.display(description=_("Fee"), ordering='fee')
  def fee_display(self, obj: BalanceTransaction) -> str:
    return get_currency_display(obj, 'fee', show_sign=True)

  @admin.display(description=_("Net"), ordering='net')
  def net_display(self, obj: BalanceTransaction) -> str:
    return get_currency_display(obj, 'net', show_sign=True)


@admin.register(Charge)
class ChargeAdmin(StripeModelAdmin[Charge]):
  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'created',
    'description',
    'status',
    'amount_display',
    'receipt_email',
    'customer_link',
    'payment_intent_link',
    'balance_transaction_link',
    'disputed',
    'refunded',
  )
  list_select_related = (
    'customer',
    'payment_intent',
    'balance_transaction',
  )

  @admin.display(description=_("Amount"), ordering='amount')
  def amount_display(self, obj: Charge) -> str:
    return get_currency_display(obj, 'amount')

  @admin.display(description=_("Customer"), ordering='customer')
  def customer_link(self, obj: Charge) -> str:
    return get_related_link(obj, 'customer')

  @admin.display(description=_("Payment Intent"), ordering='payment_intent')
  def payment_intent_link(self, obj: Charge) -> str:
    return get_related_link(obj, 'payment_intent')

  @admin.display(description=_("Balance Transaction"), ordering='balance_transaction')  # noqa: E501
  def balance_transaction_link(self, obj: Charge) -> str:
    return get_related_link(obj, 'balance_transaction')


@admin.register(Refund)
class RefundAdmin(StripeModelAdmin[Refund]):
  list_display = (
    'reason',
    'status',
    'amount_display',
    'charge_link',
    'balance_transaction_link',
  )
  list_select_related = (
    'charge',
    'balance_transaction',
  )

  @admin.display(description=_("Amount"), ordering='amount')
  def amount_display(self, obj: Refund) -> str:
    return get_currency_display(obj, 'amount')

  @admin.display(description=_("Charge"), ordering='charge')
  def charge_link(self, obj: Refund) -> str:
    return get_related_link(obj, 'charge')

  @admin.display(description=_("Balance Transaction"), ordering='balance_transaction')  # noqa: E501
  def balance_transaction_link(self, obj: Refund) -> str:
    return get_related_link(obj, 'balance_transaction')
