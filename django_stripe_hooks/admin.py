from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from django_stripe_hooks.models import (
  BalanceTransaction,
  Charge,
  ConfirmationToken,
  Coupon,
  Customer,
  Discount,
  FundingInstructions,
  Invoice,
  InvoicePayment,
  PaymentIntent,
  PaymentMethod,
  Price,
  PriceTier,
  Product,
  PromotionCode,
  Refund,
  Subscription,
)

ParentModelT = TypeVar("ParentModelT", bound=models.Model)
ChildModelT = TypeVar("ChildModelT", bound=models.Model)

# Matches django-stubs' _ListOrTuple[str] for admin field options.
_Fields = list[str] | tuple[str, ...] | tuple[()]


class StripeModelAdmin(admin.ModelAdmin[ParentModelT]):
  """ModelAdmin for Stripe models.

  Notes
  -----
  Authors are required to use Stripe SDK to create objects,
  so we remove add/change/delete permissions here.

  """

  list_display_links: tuple[str, ...] | None = None
  exclude: ClassVar[_Fields | None] = (
    "metadata",
    "currency",
  )

  class Media:
    css = {"all": ("django_stripe_hooks/css/admin.css",)}

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

  def __getattr__(self, name: str) -> Callable[[Any], str]:
    """Support `{field}_link` in list_display to generate FK admin links."""
    if name == "model" or not name.endswith("_link"):
      raise AttributeError(name)
    field_name = name.removesuffix("_link")
    try:
      field = self.model._meta.get_field(field_name)
    except FieldDoesNotExist:
      raise AttributeError(name)
    if not isinstance(field, models.ForeignKey):
      raise AttributeError(name)

    @admin.display(description=field_name.replace("_", " ").title())
    def fn(obj: Any) -> str:
      s = mark_safe("-")
      if other := getattr(obj, field_name, None):
        s = format_html(
          '<a href="{url}">{text}</a>',
          url=reverse(
            f"admin:{other._meta.app_label}_{other._meta.model_name}_change",
            args=[other.pk],
          ),
          text="View" if str(other) == other.pk else str(other),
        )
      return s

    return fn

  @admin.display(description=_("Details"))
  def detail_link(self, obj: ChildModelT) -> str:
    url = reverse(
      f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
      args=[obj.pk],
    )
    return format_html('<a href="{}">View</a>', url)


class StripeModelInline[ChildModelT: models.Model, ParentModelT: models.Model](
  admin.TabularInline[ChildModelT, ParentModelT],
):
  """Inline ModelAdmin for Stripe models.

  Notes
  -----
  Authors are required to use Stripe SDK to create objects,
  so we remove add/change/delete permissions here.

  """

  select_related: tuple[str, ...] | None = None

  readonly_fields: ClassVar[_Fields] = ("detail_link",)

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

  @admin.display(description=_("Details"))
  def detail_link(self, obj: ChildModelT) -> str:
    url = reverse(
      f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
      args=[obj.pk],
    )
    return format_html('<a href="{}">View</a>', url)


class PriceInline(StripeModelInline[Price, Product]):
  model = Price
  select_related = ("product",)
  extra = 0
  fields = (
    "nickname",
    "unit_amount",
    "billing_scheme",
    "usage_type",
    "tiers_mode",
    "active",
  )
  readonly_fields = (
    "recurring",
    "detail_link",
  )


@admin.register(Product)
class ProductAdmin(StripeModelAdmin[Product]):
  search_fields = ("name",)
  list_display_links = ("name",)
  list_display = (
    "name",
    "active",
    "deleted",
  )
  list_filter = (
    "active",
    "deleted",
  )

  inlines = (PriceInline,)


class PriceTierInline(StripeModelInline[PriceTier, Price]):
  model = PriceTier
  extra = 0
  fields = (
    "up_to",
    "flat_amount",
    "unit_amount",
    "price",
  )


@admin.register(Price)
class PriceAdmin(StripeModelAdmin[Price]):
  list_display_links = ("nickname",)
  list_display = (
    "nickname",
    "unit_amount",
    "billing_scheme",
    "recurring",
    "usage_type",
    "tiers_mode",
    "product_link",
    "active",
  )
  list_select_related = ("product",)
  list_filter = (
    "billing_scheme",
    "usage_type",
    "tiers_mode",
    "active",
  )

  inlines = (PriceTierInline,)


@admin.register(Coupon)
class CouponAdmin(StripeModelAdmin[Coupon]):
  search_fields = ("name",)
  list_display_links = ("name",)
  list_display = ("name", "terms", "redemptions", "redeem_by", "valid", "deleted")


@admin.register(PromotionCode)
class PromotionCodeAdmin(StripeModelAdmin[PromotionCode]):
  search_fields = ("code",)
  list_display = (
    "code",
    "redemptions",
    "expires_at",
    "coupon_link",
    "customer_link",
    "active",
  )
  list_select_related = (
    "coupon",
    "customer",
  )


@admin.register(Discount)
class DiscountAdmin(StripeModelAdmin[Discount]):
  search_fields = (
    "customer__name",
    "promotion_code__code",
  )
  list_display = (
    "customer_link",
    "start",
    "end",
    "coupon_link",
    "promotion_code_link",
    "subscription_link",
    "subscription_item_link",
    "invoice_link",
  )
  list_select_related = (
    "customer",
    "coupon",
    "promotion_code",
    "customer",
    "subscription",
    "subscription_item",
    "invoice",
  )


class SubscriptionInline(StripeModelInline[Subscription, Customer]):
  model = Subscription
  select_related = ("default_payment_method",)
  extra = 0
  fields = (
    "detail_link",
    "status",
    "collection_method",
    "default_payment_method",
    "cancel_at_period_end",
  )
  readonly_fields = ("detail_link",)


class PaymentMethodInline(StripeModelInline[PaymentMethod, Customer]):
  model = PaymentMethod
  extra = 0
  fields = (
    "detail_link",
    "type",
  )
  readonly_fields = ("detail_link",)


class FundingInstructionsInline(StripeModelInline[FundingInstructions, Customer]):
  model = FundingInstructions
  extra = 0
  fields = (
    "account_holder_name",
    "account_type",
    "bank_name",
    "account_number",
    "routing_number",
    "swift_code",
    "bank_address",
    "account_holder_address",
  )


class InvoiceMixin:
  exclude: ClassVar[_Fields | None] = (
    "invoice_pdf",
    "hosted_invoice_url",
  )
  readonly_fields: ClassVar[_Fields] = (
    "pdf_link",
    "portal_link",
    "detail_link",
  )

  @admin.display(description=_("PDF"))
  def pdf_link(self, obj: Invoice) -> str:
    s = mark_safe("-")
    if obj.invoice_pdf:
      s = format_html(
        '<a href="{}" target="_blank">View</a>',
        obj.invoice_pdf,
      )
    return s

  @admin.display(description=_("Portal"))
  def portal_link(self, obj: Invoice) -> str:
    s = mark_safe("-")
    if obj.hosted_invoice_url:
      s = format_html(
        '<a href="{}" target="_blank">Link</a>',
        obj.hosted_invoice_url,
      )
    return s


class InvoiceInline(InvoiceMixin, StripeModelInline[Invoice, Customer]):
  model = Invoice
  extra = 0
  fields = (
    "detail_link",
    "status",
    "total",
    "period_start",
    "period_end",
    "collection_method",
    "pdf_link",
    "portal_link",
  )


# TODO: Deleted customers have no name
@admin.register(Customer)
class CustomerAdmin(StripeModelAdmin[Customer]):
  search_fields = (
    "name",
    "email",
    "phone",
  )
  list_display_links = ("name",)
  list_display = (
    "name",
    "email",
    "phone",
    "deleted",
  )

  inline_type = "stacked"
  inlines = (
    SubscriptionInline,
    InvoiceInline,
    PaymentMethodInline,
    FundingInstructionsInline,
  )


@admin.register(PaymentMethod)
class PaymentMethodAdmin(StripeModelAdmin[PaymentMethod]):
  search_fields = (
    "customer__email",
    "customer__name",
    "customer__phone",
    "type",
  )
  list_display = (
    "detail_link",
    "type",
    "customer_link",
  )
  list_select_related = ("customer",)
  list_filter = ("type",)
  readonly_fields = ("detail_link",)


@admin.register(FundingInstructions)
class FundingInstructionsAdmin(StripeModelAdmin[FundingInstructions]):
  list_display = (
    "customer_link",
    "bank_name",
    "account_number",
    "routing_number",
    "swift_code",
    "bank_address",
    "account_holder_address",
  )
  list_select_related = ("customer",)


@admin.register(PaymentIntent)
class PaymentIntentAdmin(StripeModelAdmin[PaymentIntent]):
  search_fields = (
    "description",
    "status",
    "customer__email",
    "customer__name",
    "customer__phone",
    "description",
  )
  list_display = (
    "description",
    "status",
    "amount",
    "setup_future_usage",
    # 'receipt_email',
    "customer_link",
    "payment_method_link",
  )
  list_select_related = (
    "customer",
    "payment_method",
  )
  list_filter = (
    "description",
    "status",
  )


# TODO: None
@admin.register(ConfirmationToken)
class ConfirmationTokenAdmin(StripeModelAdmin[ConfirmationToken]):
  list_display = (
    "id",
    "created",
    "expires_at",
    "payment_method_preview",
    "customer_link",
    "is_expired",
  )
  list_select_related = ("customer",)


@admin.register(Subscription)
class SubscriptionAdmin(StripeModelAdmin[Subscription]):
  search_fields = (
    "customer__email",
    "customer__name",
    "customer__phone",
  )
  list_display = (
    "customer_link",
    "status",
    "collection_method",
    "default_payment_method_link",
    "cancel_at_period_end",
  )
  list_select_related = (
    "customer",
    "default_payment_method",
  )
  list_filter = (
    "collection_method",
    "cancel_at_period_end",
  )


# TODO: Review table column order
@admin.register(Invoice)
class InvoiceAdmin(InvoiceMixin, StripeModelAdmin[Invoice]):
  search_fields = (
    "number",
    "status",
    "collection_method",
    "customer__email",
    "customer__name",
    "customer__phone",
  )
  list_display = (
    "number",
    "status",
    "total",
    "period_start",
    "period_end",
    "collection_method",
    "pdf_link",
    "portal_link",
    "customer_link",
    "subscription_link",
  )
  list_select_related = (
    "customer",
    "subscription",
  )
  list_filter = (
    "collection_method",
    "status",
  )


# TODO: None?
@admin.register(InvoicePayment)
class InvoicePaymentAdmin(StripeModelAdmin[InvoicePayment]):
  list_display = (
    "created",
    "status",
    "amount_paid",
    "amount_requested",
    "payment_intent_link",
    "invoice_link",
    "is_default",
  )
  list_select_related = (
    "payment_intent",
    "invoice",
  )


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(StripeModelAdmin[BalanceTransaction]):
  search_fields = (
    "type",
    "status",
  )
  list_display = (
    "type",
    "status",
    "available_on",
    "amount",
    "fee",
    "net",
  )
  list_filter = (
    "type",
    "status",
  )


@admin.register(Charge)
class ChargeAdmin(StripeModelAdmin[Charge]):
  search_fields = (
    "description",
    "status",
    "receipt_email",
    "customer__email",
    "customer__name",
    "customer__phone",
  )
  list_display = (
    "customer_link",
    "description",
    "status",
    "amount",
    # 'receipt_email',
    "payment_intent_link",
    "balance_transaction_link",
    "disputed",
    "refunded",
  )
  list_select_related = (
    "customer",
    "payment_intent",
    "balance_transaction",
  )
  list_filter = (
    "description",
    "status",
    "disputed",
    "refunded",
  )


@admin.register(Refund)
class RefundAdmin(StripeModelAdmin[Refund]):
  search_fields = (
    "reason",
    "status",
  )
  list_display = (
    "reason",
    "status",
    "amount",
    "charge_link",
    "balance_transaction_link",
  )
  list_select_related = (
    "charge",
    "balance_transaction",
  )
  list_filter = (
    "status",
    "reason",
  )
