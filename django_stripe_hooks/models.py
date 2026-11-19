from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, TypeVar

import stripe
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from django_stripe_hooks.fields import MoneyField
from django_stripe_hooks.managers import (
  FundingInstructionsManager,
  StripeManager,
  _write_depth,
  is_stripe_model,
)
from django_stripe_hooks.money import CURRENCY_CHOICES, CURRENCY_DIVISOR

T = TypeVar("T", bound=stripe.StripeObject)


class StripeModel[T: stripe.StripeObject](models.Model):
  """Common Stripe model methods and properties."""

  API_EXPAND_FIELDS: tuple[str, ...] = ()
  WEBHOOK_EVENTS: tuple[str, ...] = ()

  id = models.CharField(
    max_length=255,
    primary_key=True,
    verbose_name=_("Stripe ID"),
  )
  metadata = models.JSONField(
    default=dict,
    verbose_name=_("Metadata"),
    help_text=_("Metadata for internal use only"),
  )

  objects = StripeManager()

  class Meta:
    abstract = True

  def __str__(self) -> str:
    return self.id

  def save(self, *args: Any, **kwargs: Any) -> None:
    if getattr(_write_depth, "count", 0) == 0:
      raise TypeError(
        f"{self.__class__.__name__} is managed by Stripe. "
        "Create and update objects via the Stripe SDK; "
        "the local model is updated automatically when the webhook arrives. "
        f"To sync immediately, call {self.__class__.__name__}.objects.from_stripe()."
      )
    super().save(*args, **kwargs)

  def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
    if getattr(_write_depth, "count", 0) == 0:
      raise TypeError(
        f"{self.__class__.__name__} is managed by Stripe. "
        "Delete objects via the Stripe SDK; the local model "
        "is soft-deleted automatically when the webhook arrives."
      )
    return super().delete(*args, **kwargs)

  @classmethod
  def clean_stripe(
    cls,
    field: models.Field[Any, Any] | models.ForeignObjectRel,
    value: Any,
    currency: str = "",
  ) -> Any:
    """Cleans a scalar value from the Stripe API for a Django field."""
    if isinstance(field, (models.CharField, models.TextField)):
      if (value is None) and not field.null:
        value = ""
    elif isinstance(field, models.DateTimeField):
      if field.null and (value is None):
        pass
      else:
        value = dt.datetime.fromtimestamp(value, tz=dt.UTC)
    elif isinstance(field, models.DateField):
      if field.null and (value is None):
        pass
      else:
        value = dt.date.fromtimestamp(value)
    elif isinstance(field, models.BooleanField):
      if (value is None) and not field.null:
        value = False
    elif isinstance(field, models.IntegerField):
      if (value is None) and not field.null:
        value = 0
    elif isinstance(field, MoneyField):
      if value is None:
        value = None if field.null else Decimal(0)
      else:
        value = Decimal(value / CURRENCY_DIVISOR[currency])
    elif isinstance(field, models.JSONField):
      if value is None:
        value = None if field.null else field.default()
      else:
        value = field.default(getattr(value, "data", value))
    return value

  @classmethod
  def deserialize(
    cls,
    stripe_obj: T,
  ) -> dict[str, Any]:
    """Convert a StripeObject (or plain dict) to a nested Django field dict.

    FK fields with an expanded object produce ``data[field.name] = {...}``.
    FK fields with only a string ID produce ``data[field.attname] = "id"``.
    Reverse FK / M2M fields produce ``data[field.name] = [{...}, ...]``.
    """
    data: dict[str, Any] = {}

    for field in cls._meta.get_fields():
      if field.name not in stripe_obj:
        continue

      value = stripe_obj[field.name]

      if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        if isinstance(value, stripe.StripeObject):
          data[field.name] = value
        elif isinstance(value, str):
          data[field.attname] = value

      elif isinstance(field, models.ManyToOneRel):
        RelatedModel = field.related_model
        if is_stripe_model(RelatedModel):
          items = getattr(value, "data", value)
          if isinstance(items, dict):
            items = items.get("data", [])
          data[field.name] = items

      elif isinstance(field, models.ManyToManyField):
        data[field.name] = [getattr(v, "id", v) for v in value]

      else:
        currency = stripe_obj.get("currency", "")
        data[field.name] = cls.clean_stripe(field, value, currency=currency)

    return data


class Product(StripeModel[stripe.Product]):
  """Django implementation of Stripe Products.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/products

  """

  WEBHOOK_EVENTS = (
    "product.created",
    "product.deleted",
    "product.updated",
  )

  name = models.CharField(
    max_length=255,
    unique=True,  # Create index
    verbose_name=_("Name"),
    help_text=_("The product's name, meant to be displayed to the customer"),
  )
  description = models.TextField(
    verbose_name=_("Description"),
    help_text=_("The product's description, meant to be displayed to the customer"),
  )
  statement_descriptor = models.CharField(
    max_length=22,
    validators=[
      RegexValidator(
        regex=r"^[A-Za-z0-9]+$",
        message=_("Code must be alphanumeric"),
      )
    ],
    verbose_name=_("Statement descriptor"),
    help_text=_("This will appear on the customer's bank statement"),
  )
  active = models.BooleanField(
    verbose_name=_("Active?"),
  )
  deleted = models.BooleanField(
    default=False,
    verbose_name=_("Deleted?"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Product")
    verbose_name_plural = _("Products")
    ordering = ["pk"]

  def __str__(self) -> str:
    return self.name or self.id


class Price(StripeModel[stripe.Price]):
  """Django implementation of Stripe Prices.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/prices

  """

  API_EXPAND_FIELDS = ("product",)
  WEBHOOK_EVENTS = (
    "price.created",
    "price.deleted",
    "price.updated",
  )

  class Type(models.TextChoices):
    RECURRING = "recurring", _("Recurring charge")
    ONE_TIME = "one_time", _("One time charge")

  class Interval(models.TextChoices):
    NA = "", _("N/A")
    DAY = "day", _("Day")
    WEEK = "week", _("Week")
    MONTH = "month", _("Month")
    YEAR = "year", _("Year")

  class UsageType(models.TextChoices):
    NA = "", _("N/A")
    LICENSED = "licensed", _("Licensed")
    METERED = "metered", _("Metered")

  class BillingScheme(models.TextChoices):
    PER_UNIT = "per_unit", _("Per unit")
    TIERED = "tiered", _("Tiered")

  class TiersMode(models.TextChoices):
    NA = "na", _("N/A")
    VOLUME = "volume", _("Volume")
    GRADUATED = "graduated", _("Graduated")

  nickname = models.CharField(
    max_length=255,
    verbose_name=_("Nickname"),
    help_text=_("A brief description of the plan visible to customers"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  unit_amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Unit amount"),
    help_text=_("Amount to be charged (per interval for recurring payments)"),
  )
  billing_scheme = models.CharField(
    max_length=10,
    choices=BillingScheme.choices,
    verbose_name=_("Billing scheme"),
    help_text=("How to compute the price per period"),
  )
  type = models.CharField(
    max_length=10,
    choices=Type.choices,
    verbose_name=_("Type"),
    help_text=_("One-time or recurring charge"),
  )
  interval = models.CharField(
    max_length=10,
    choices=Interval.choices,
    blank=True,
    verbose_name=_("Interval"),
    help_text=_("Required for recurring payments"),
  )
  interval_count = models.PositiveIntegerField(
    null=True,
    verbose_name=_("Interval count"),
    help_text=_("Number of intervals between subscription billings"),
  )
  usage_type = models.CharField(
    max_length=10,
    blank=True,
    choices=UsageType.choices,
    verbose_name=_("Usage type"),
    help_text=("Determines how the quantity per period should be determined"),
  )
  tiers_mode = models.CharField(
    max_length=10,
    choices=TiersMode.choices,
    verbose_name=_("Tiers mode"),
    help_text=(
      "In volume-based tiering, the maximum quantity within a period determines the per unit price. "  # noqa: E501
      "In graduated tiering, the pricing can change as the quantity grows."
    ),
  )
  product = models.ForeignKey(
    Product,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="prices",
    verbose_name=_("Product"),
  )
  active = models.BooleanField(verbose_name=_("Active?"))

  class Meta(StripeModel.Meta):
    verbose_name = _("Price")
    verbose_name_plural = _("Price")

  def __str__(self) -> str:
    return self.nickname or self.id

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Price) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if (recurring := getattr(stripe_obj, "recurring", None)) is not None:
      data.update(
        {
          "interval": recurring.interval,
          "interval_count": recurring.interval_count,
          "usage_type": recurring.usage_type,
        }
      )

    return data

  @property
  def recurring(self) -> str:
    if self.type == "recurring":
      if self.interval_count == 1:
        s = f"Every {self.interval}"
      else:
        s = f"Every {self.interval_count} {self.interval}s"
    else:
      s = "One-time charge"
    return s


class PriceTier(models.Model):
  """Django implementation of a Price Tier for Stripe's Tiered Pricing.

  Notes
  -----

  Stripe Docs: https://docs.stripe.com/api/prices/object#price_object-tiers

  """

  up_to = models.PositiveIntegerField(
    blank=True,
    null=True,
    verbose_name=_("Up to"),
    help_text=_("Quantity upper bound, leave blank for no upper bound"),
  )
  flat_amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    currency_field="price__currency",
    verbose_name=_("Flat amount"),
    help_text=_("Base amount for the tier"),
  )
  unit_amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    currency_field="price__currency",
    verbose_name=_("Unit amount"),
    help_text=_("Per unit cost in the tier"),
  )
  price = models.ForeignKey(
    Price,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="tiers",
    verbose_name=_("Price"),
  )

  def __str__(self) -> str:
    if self.up_to is None:
      s = f"Tier: {self.flat_amount} + {self.unit_amount} per unit"
    else:
      s = f"Tier: {self.flat_amount} + {self.unit_amount} per unit, up to {self.up_to}"
    return s

  @classmethod
  def deserialize(cls, stripe_obj: stripe.StripeObject) -> dict[str, Any]:
    return dict(stripe_obj)


class Coupon(StripeModel[stripe.Coupon]):
  """Django implementation of Stripe Coupons.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/coupons

  """

  API_EXPAND_FIELDS = ("applies_to",)
  WEBHOOK_EVENTS = (
    "coupon.created",
    "coupon.deleted",
    "coupon.updated",
  )

  class Duration(models.TextChoices):
    ONCE = "once", _("Once")
    FOREVER = "forever", _("Forever")

  name = models.CharField(
    max_length=255,
    blank=False,
    verbose_name=_("Name"),
    help_text=_("Name to display on invoices"),
  )
  percent_off = models.PositiveIntegerField(
    null=True,
    blank=True,
    verbose_name=_("Percent off"),
    help_text=_("Percent that will be taken off of a subscription"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount_off = MoneyField(
    max_digits=8,
    decimal_places=2,
    null=True,
    blank=True,
    verbose_name=_("Amount off"),
    help_text=_("Amount that will be taken off of a subscription"),
  )
  duration = models.CharField(
    max_length=9,
    choices=Duration.choices,
    verbose_name=_("Duration"),
    help_text=_("How long the discount will apply to a customer's subscription"),
  )
  times_redeemed = models.PositiveIntegerField(
    default=0,
    editable=False,
    verbose_name=_("Current number of redemptions"),
  )
  max_redemptions = models.PositiveIntegerField(
    default=0,
    verbose_name=_("Maximum number of redemptions"),
    help_text=_("Optional, leave blank for infinite redemptions"),
  )
  redeem_by = models.DateTimeField(
    null=True,
    blank=True,
    verbose_name=_("Redeem by"),
  )
  products = models.ManyToManyField(
    Product,
    related_name="coupons",
    blank=True,
    verbose_name=_("Products"),
    help_text=_("Specify which product(s) this coupon will apply to"),
  )
  valid = models.BooleanField(
    verbose_name=_("Valid?"),
  )
  deleted = models.BooleanField(
    default=False,
    verbose_name=_("Deleted?"),
  )

  def __str__(self) -> str:
    return self.name or self.id

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Coupon) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if getattr(stripe_obj, "deleted", False):
      data.setdefault("name", f"Deleted ({stripe_obj.id})")
      data.setdefault("currency", "")
      data.setdefault("duration", "")
      data.setdefault("valid", False)

    if (applies_to := getattr(stripe_obj, "applies_to", None)) is not None:
      data["products"] = list(applies_to.products)

    return data

  @property
  def terms(self) -> str:
    if self.percent_off:
      terms = f"{self.percent_off}% off {self.duration}"
    elif self.amount_off:
      terms = f"${self.amount_off} off {self.duration}"
    else:
      terms = ""
    return terms

  @property
  def redemptions(self) -> str:
    count = f"{self.times_redeemed:,d}"
    if self.max_redemptions:
      maximum = f"{self.max_redemptions:,d}"
    else:
      maximum = "&infin;"
    return mark_safe(f"{count} of {maximum}")


class PromotionCode(StripeModel[stripe.PromotionCode]):
  """Django implementation of Stripe Promotion Codes.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/promotion_codes

  """

  API_EXPAND_FIELDS = ("promotion.coupon",)
  WEBHOOK_EVENTS = (
    "promotion_code.created",
    "promotion_code.updated",
  )

  code = models.CharField(
    max_length=100,
    unique=True,
    validators=[
      RegexValidator(
        regex=r"^[A-Za-z0-9]+$",
        message=_("Code must be alphanumeric"),
      )
    ],
    verbose_name=_("Code"),
    help_text=_("Alphanumeric code with no spaces or symbols"),
  )
  times_redeemed = models.PositiveIntegerField(
    default=0,
    editable=False,
    verbose_name=_("Current number of redemptions"),
  )
  max_redemptions = models.PositiveIntegerField(
    default=0,
    verbose_name=_("Maximum number of redemptions"),
    help_text=_("Optional, leave blank for infinite redemptions"),
  )
  expires_at = models.DateField(
    blank=True,
    null=True,
    verbose_name=_("Expires on"),
    help_text=_("Optional, leave blank for no expiration date"),
  )
  coupon = models.ForeignKey(
    Coupon,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="promotion_codes",
    verbose_name=_("Coupon"),
  )
  customer = models.ForeignKey(
    "Customer",
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    null=True,
    blank=True,
    verbose_name=_("Customer"),
  )
  active = models.BooleanField(verbose_name=_("Active?"))

  class Meta(StripeModel.Meta):
    verbose_name = _("Promotion Code")
    verbose_name_plural = _("Promotion Codes")

  def __str__(self) -> str:
    return self.code or self.id

  def save(self, *args: Any, **kwargs: Any) -> None:
    """Extra logic to determine if a promotion code should be deactivated.

    Notes
    -----
    This /should/ be done by Stripe Webhooks, a bug report has been filed.
    Once it's confirmed, we can remove this whole method.

    """
    if self.expires_at and (self.expires_at < dt.date.today()):
      # Confirmed 02/13/2026: this has not been fixed.
      self.active = False
    super().save(*args, **kwargs)

  @classmethod
  def deserialize(cls, stripe_obj: stripe.PromotionCode) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if stripe_obj.promotion:
      stripe_coupon = stripe_obj.promotion.coupon
      if isinstance(stripe_coupon, stripe.Coupon):
        data["coupon"] = stripe_coupon
      elif isinstance(stripe_coupon, str):
        data["coupon_id"] = stripe_coupon

    return data

  @property
  def redemptions(self) -> str:
    count = f"{self.times_redeemed:,d}"
    if self.max_redemptions:
      maximum = f"{self.max_redemptions:,d}"
    else:
      maximum = "&infin;"
    return mark_safe(f"{count} of {maximum}")


class Discount(StripeModel[stripe.Discount]):
  """Django implementation of Stripe Discounts.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/discounts

  """

  API_EXPAND_FIELDS = (
    "customer",
    "promotion_code",
    "source.coupon",
  )
  WEBHOOK_EVENTS = (
    "customer.discount.created",
    "customer.discount.deleted",
    "customer.discount.updated",
  )

  start = models.DateTimeField(
    verbose_name=_("Start time"),
  )
  end = models.DateTimeField(
    blank=True,
    null=True,
    verbose_name=_("End time"),
  )
  coupon = models.ForeignKey(
    Coupon,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    verbose_name=_("Coupon"),
  )
  promotion_code = models.ForeignKey(
    PromotionCode,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    null=True,
    blank=True,
  )
  customer = models.ForeignKey(
    "Customer",
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    verbose_name=_("Customer"),
    null=True,
    blank=True,
  )
  subscription = models.ForeignKey(
    "Subscription",
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    verbose_name=_("Subscription"),
    null=True,
    blank=True,
  )
  subscription_item = models.ForeignKey(
    "SubscriptionItem",
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    verbose_name=_("Subscription items"),
    null=True,
    blank=True,
  )
  invoice = models.ForeignKey(
    "Invoice",
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="discounts",
    null=True,
    blank=True,
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Discount")
    verbose_name_plural = _("Discounts")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Discount) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if (source := getattr(stripe_obj, "source", None)) is not None:
      stripe_coupon = source.coupon
      if isinstance(stripe_coupon, stripe.Coupon):
        data["coupon"] = stripe_coupon
      elif isinstance(stripe_coupon, str):
        data["coupon_id"] = stripe_coupon

    return data


class Customer(StripeModel[stripe.Customer]):
  """Django implementation of Stripe Customers.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/customers

  """

  WEBHOOK_EVENTS = (
    "customer.created",
    "customer.deleted",
    "customer.updated",
  )

  name = models.CharField(
    max_length=255,
    blank=True,
    verbose_name=_("Name"),
  )
  email = models.EmailField(
    db_index=True,
    verbose_name=_("Email address"),
  )
  phone = models.CharField(
    max_length=20,
    blank=True,
    verbose_name=_("Phone number"),
  )
  deleted = models.BooleanField(
    default=False,
    verbose_name=_("Deleted?"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Customer")
    verbose_name_plural = _("Customers")
    ordering = ["email"]

  def __str__(self) -> str:
    return self.name or self.id


class PaymentMethod(StripeModel[stripe.PaymentMethod]):
  """Django implementation of Stripe PaymentMethods.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/payment_methods

  """

  API_EXPAND_FIELDS = ("customer",)
  WEBHOOK_EVENTS = (
    "payment_method.attached",
    "payment_method.automatically_updated",
    "payment_method.detached",
    "payment_method.updated",
  )

  class Type(models.TextChoices):
    ACSS_DEBIT = "acss_debit", _("ACSS Debit (Canada/US)")
    AFFIRM = "affirm", _("Affirm")
    AFTERPAY_CLEARPAY = "afterpay_clearpay", _("Afterpay / Clearpay")
    ALIPAY = "alipay", _("Alipay")
    ALMA = "alma", _("Alma")
    AMAZON_PAY = "amazon_pay", _("Amazon Pay")
    AU_BECS_DEBIT = "au_becs_debit", _("BECS Debit (Australia)")
    BACS_DEBIT = "bacs_debit", _("BACS Debit (UK)")
    BANCONTACT = "bancontact", _("Bancontact (Belgium)")
    BILLIE = "billie", _("Billie")
    BLIK = "blik", _("BLIK (Poland)")
    BOLETO = "boleto", _("Boleto (Brazil)")
    CARD = "card", _("Credit/Debit Card")
    CARD_PRESENT = "card_present", _("Card Present")
    CASHAPP = "cashapp", _("Cash App Pay")
    CRYPTO = "crypto", _("Crypto")
    CUSTOM = "custom", _("Custom")
    CUSTOMER_BALANCE = "customer_balance", _("Customer Balance")
    EPS = "eps", _("EPS (Austria)")
    FPX = "fpx", _("FPX (Malaysia)")
    GIROPAY = "giropay", _("giropay (Germany)")
    GRABPAY = "grabpay", _("GrabPay (Southeast Asia)")
    IDEAL = "ideal", _("iDEAL (Netherlands)")
    INTERAC_PRESENT = "interac_present", _("Interac (Terminal)")
    KAKAO_PAY = "kakao_pay", _("Kakao Pay (South Korea)")
    KLARNA = "klarna", _("Klarna")
    KONBINI = "konbini", _("Konbini (Japan)")
    KR_CARD = "kr_card", _("Korean Card")
    LINK = "link", _("Link")
    MB_WAY = "mb_way", _("MB WAY (Portugal)")
    MOBILEPAY = "mobilepay", _("MobilePay (Scandinavia)")
    MULTIBANCO = "multibanco", _("Multibanco (Portugal)")
    NAVER_PAY = "naver_pay", _("Naver Pay (South Korea)")
    NZ_BANK_ACCOUNT = "nz_bank_account", _("Bank Account (New Zealand)")
    OXXO = "oxxo", _("OXXO (Mexico)")
    P24 = "p24", _("Przelewy24 (Poland)")
    PAY_BY_BANK = "pay_by_bank", _("Pay by Bank")
    PAYCO = "payco", _("PAYCO (South Korea)")
    PAYNOW = "paynow", _("PayNow (Singapore)")
    PAYPAL = "paypal", _("PayPal")
    PAYTO = "payto", _("PayTo (Australia)")
    PIX = "pix", _("Pix (Brazil)")
    PROMPTPAY = "promptpay", _("PromptPay (Thailand)")
    REVOLUT_PAY = "revolut_pay", _("Revolut Pay")
    SAMSUNG_PAY = "samsung_pay", _("Samsung Pay")
    SATISPAY = "satispay", _("Satispay (Italy)")
    SEPA_DEBIT = "sepa_debit", _("SEPA Direct Debit (EU)")
    SOFORT = "sofort", _("Sofort (Europe)")
    SWISH = "swish", _("Swish (Sweden)")
    TWINT = "twint", _("TWINT (Switzerland)")
    US_BANK_ACCOUNT = "us_bank_account", _("Bank Account (US)")
    WECHAT_PAY = "wechat_pay", _("WeChat Pay")
    ZIP = "zip", _("Zip")

  CARD_BRANDS = (
    ("amex", _("American Express")),
    ("cartes_bancaires", _("Cartes Bancaires")),
    ("diners", _("Diners Club")),
    ("discover", _("Discover")),
    ("eftpos_au", _("EFTPOS Australia")),
    ("jcb", _("JCB")),
    ("link", _("Link")),
    ("mastercard", _("MasterCard")),
    ("unionpay", _("UnionPay")),
    ("visa", _("Visa")),
    ("unknown", _("Unknown")),
  )

  type = models.CharField(
    max_length=17,
    choices=Type.choices,
    verbose_name=_("Type"),
  )
  details = models.JSONField(
    default=dict,
    verbose_name=_("Details"),
  )
  billing_details = models.JSONField(
    default=dict,
    verbose_name=_("Billing details"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="payment_methods",
    null=True,
    blank=True,
    verbose_name=_("Customer"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Payment Method")
    verbose_name_plural = _("Payment Methods")

  def __str__(self) -> str:
    if self.type == "card":
      s = "{brand} ({last4})".format(
        brand=dict(self.CARD_BRANDS)[self.details.get("brand", "unknown")],
        last4=self.details.get("last4", "0000"),
      )
    else:  # TODO add more formats
      s = self.get_type_display()
    return s

  @classmethod
  def deserialize(cls, stripe_obj: stripe.PaymentMethod) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)
    data["details"] = getattr(stripe_obj, stripe_obj.type)
    return data


class FundingInstructions(models.Model):
  """Django implementation of Stripe Funding Instructions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/issuing/funding_instructions

  """

  customer = models.OneToOneField(
    Customer,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="funding_instructions",
    verbose_name=_("Customer"),
  )
  account_holder_name = models.CharField(
    max_length=255,
    verbose_name=_("Account holder name"),
  )
  account_type = models.CharField(
    max_length=10,
    verbose_name=_("Account type"),
  )
  account_number = models.CharField(
    max_length=17,
    verbose_name=_("Account number"),
  )
  bank_name = models.CharField(
    max_length=255,
    verbose_name=_("Bank name"),
  )
  routing_number = models.CharField(
    max_length=9,
    verbose_name=_("Routing number"),
  )
  swift_code = models.CharField(
    max_length=11,
    verbose_name=_("SWIFT code"),
  )
  account_holder_address = models.JSONField(
    default=dict,
    verbose_name=_("Account holder address"),
  )
  bank_address = models.JSONField(
    default=dict,
    verbose_name=_("Bank address"),
  )

  objects = FundingInstructionsManager()

  class Meta:
    verbose_name = _("Funding Instructions")
    verbose_name_plural = _("Funding Instructions")

  def __str__(self) -> str:
    return self.bank_name

  @classmethod
  def deserialize(
    cls,
    stripe_obj: stripe.FundingInstructions,
  ) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for fa in stripe_obj.bank_transfer.financial_addresses:
      if (fa.type == "aba") and (fa.aba is not None):
        data.update(
          {
            "account_holder_address": dict(fa.aba.account_holder_address),
            "account_holder_name": fa.aba.account_holder_name,
            "account_number": fa.aba.account_number,
            "account_type": fa.aba.account_type,
            "bank_address": dict(fa.aba.bank_address),
            "bank_name": fa.aba.bank_name,
            "routing_number": fa.aba.routing_number,
          }
        )
      elif (fa.type == "swift") and (fa.swift is not None):
        data["swift_code"] = fa.swift.swift_code

    return data


class PaymentIntent(StripeModel[stripe.PaymentIntent]):
  """Django implementation of Stripe Payment Intents.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/payment_intents

  """

  API_EXPAND_FIELDS = ("customer", "payment_method")
  WEBHOOK_EVENTS = (
    "payment_intent.amount_capturable_updated",
    "payment_intent.canceled",
    "payment_intent.created",
    "payment_intent.partially_funded",
    "payment_intent.payment_failed",
    "payment_intent.processing",
    "payment_intent.requires_action",
    "payment_intent.succeeded",
  )

  class FutureUsage(models.TextChoices):
    ON_SESSION = "on_session", _("On Session")
    OFF_SESSION = "off_session", _("Off Session")

  class Status(models.TextChoices):
    REQUIRES_PAYMENT_METHOD = "requires_payment_method", _("Requires payment method")
    REQUIRES_CONFIRMATION = "requires_confirmation", _("Requires confirmation")
    REQUIRES_ACTION = "requires_action", _("Requires action")
    PROCESSING = "processing", _("Processing")
    REQUIRES_CAPTURE = "requires_capture", _("Requires capture")
    CANCELED = "canceled", _("Canceled")
    SUCCEEDED = "succeeded", _("Succeeded")

  description = models.CharField(
    max_length=255,
    verbose_name=_("Description"),
    help_text=_("Description of this charge that will be displayed to customers"),
  )
  status = models.CharField(
    max_length=23,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
    help_text=_("Amount to be collected"),
  )
  setup_future_usage = models.CharField(
    max_length=11,
    choices=FutureUsage.choices,
    verbose_name=_("Setup future usage"),
  )
  last_payment_error = models.JSONField(
    default=dict,
    verbose_name=_("Last payment error"),
  )
  next_action = models.JSONField(
    default=dict,
    verbose_name=_("Next action"),
  )
  payment_method_types = models.JSONField(
    default=list,
    verbose_name=_("Payment method types"),
  )
  receipt_email = models.EmailField(
    verbose_name=_("Receipt email"),
  )
  billing_details = models.JSONField(
    default=dict,
    verbose_name=_("Billing details"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="payment_intents",
    verbose_name=_("Customer"),
  )
  payment_method = models.ForeignKey(
    PaymentMethod,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="payment_intents",
    blank=True,
    null=True,
    verbose_name=_("Payment method"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Payment Intent")
    verbose_name_plural = _("Payment Intents")


class ConfirmationToken(models.Model):
  """Django implementation of Stripe Confirmation Tokens.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/confirmation_tokens

  """

  id = models.CharField(
    max_length=255,
    unique=True,
    primary_key=True,
  )
  created = models.DateTimeField(
    verbose_name=_("Created at"),
  )
  expires_at = models.DateTimeField(
    verbose_name=_("Expires at"),
  )
  payment_method_preview = models.JSONField(
    default=dict,
    verbose_name=_("Payment method preview"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="confirmation_tokens",
    verbose_name=_("Customer"),
  )

  class Meta:
    verbose_name = _("Confirmation Token")
    verbose_name_plural = _("Confirmation Tokens")
    ordering = ["customer", "-created"]
    get_latest_by = "created"

  def __str__(self) -> str:
    pm_type = self.payment_method_preview.get("type", "unknown")
    pm = PaymentMethod(
      type=pm_type,
      details=self.payment_method_preview[pm_type],
    )
    return str(pm)

  @property
  def is_expired(self) -> bool:
    return self.expires_at < timezone.now()


class Subscription(StripeModel[stripe.Subscription]):
  """Django implementation of Stripe Subscriptions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/subscriptions

  """

  API_EXPAND_FIELDS = (
    "customer",
    "discounts.promotion_code.coupon",
    "default_payment_method.customer",
  )
  WEBHOOK_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.pending_update_applied",
    "customer.subscription.pending_update_expired",
    "customer.subscription.resumed",
    "customer.subscription.trial_will_end",
    "customer.subscription.updated",
  )

  class Status(models.TextChoices):
    INCOMPLETE = "incomplete", _("Incomplete")
    INCOMPLETE_EXPIRED = "incomplete_expired", _("Incomplete expired")
    TRIALING = "trialing", _("Trial")
    ACTIVE = "active", _("Active")
    PAST_DUE = "past_due", _("Past due")
    CANCELED = "canceled", _("Canceled")
    UNPAID = "unpaid", _("Unpaid")

    @classmethod
    def active_statuses(cls) -> tuple[Subscription.Status, ...]:
      """Statuses that grant access to subscription features."""
      return (cls.TRIALING, cls.ACTIVE)

    @classmethod
    def terminal_statuses(cls) -> tuple[Subscription.Status, ...]:
      """Statuses a subscription cannot recover from."""
      return (cls.INCOMPLETE_EXPIRED, cls.CANCELED)

  class CollectionMethod(models.TextChoices):
    CHARGE_AUTOMATICALLY = "charge_automatically", _("Charge automatically")
    SEND_INVOICE = "send_invoice", _("Send invoice")

  status = models.CharField(
    max_length=18,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  cancel_at_period_end = models.BooleanField(
    verbose_name=_("Cancel at period end?"),
  )
  collection_method = models.CharField(
    max_length=20,
    choices=CollectionMethod.choices,
    verbose_name=_("Collection method"),
    help_text=_(
      "Charge using the default source on file or email invoice with instructions"
    ),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="subscriptions",
    verbose_name=_("Customer"),
  )
  default_payment_method = models.ForeignKey(
    PaymentMethod,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="subscriptions",
    blank=True,
    null=True,
    verbose_name=_("Default payment method"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Subscription")
    verbose_name_plural = _("Subscriptions")


class SubscriptionItem(StripeModel[stripe.SubscriptionItem]):
  """Django implementation of Stripe Subscription Items.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/subscription_items

  """

  current_period_start = models.DateTimeField(
    verbose_name=_("Current period start"),
  )
  current_period_end = models.DateTimeField(
    verbose_name=_("Current period end"),
  )
  price = models.ForeignKey(
    Price,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="subscription_items",
    verbose_name=_("Price"),
  )
  quantity = models.PositiveIntegerField(
    verbose_name=_("Quantity"),
  )
  subscription = models.ForeignKey(
    Subscription,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="items",
    verbose_name=_("Subscription"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Subscription Item")
    verbose_name_plural = _("Subscription Items")

    constraints = [
      models.UniqueConstraint(
        fields=["price", "subscription"],
        name="unique_item",
      ),
    ]


class Invoice(StripeModel[stripe.Invoice]):
  """Django implementation of Stripe Invoices.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/invoices

  """

  API_EXPAND_FIELDS = (
    "customer",
    "discounts",
    "parent.subscription_details.subscription.discounts",
    "payments.data.payment.payment_intent",
  )
  WEBHOOK_EVENTS = (
    "invoice.created",
    "invoice.deleted",
    "invoice.finalization_failed",
    "invoice.finalized",
    "invoice.marked_uncollectible",
    "invoice.overdue",
    "invoice.paid",
    "invoice.payment_action_required",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
    "invoice.sent",
    "invoice.updated",
    "invoice.voided",
    "invoice.will_be_due",
  )

  class CollectionMethod(models.TextChoices):
    CHARGE_AUTOMATICALLY = "charge_automatically", _("Charge automatically")
    SEND_INVOICE = "send_invoice", _("Send invoice")

  class Status(models.TextChoices):
    DRAFT = "draft", _("Draft")
    OPEN = "open", _("Open")
    PAID = "paid", _("Paid")
    UNCOLLECTIBLE = "uncollectible", _("Uncollectible")
    VOID = "void", _("Void")

  created = models.DateTimeField(
    verbose_name=_("Created"),
  )
  number = models.CharField(
    max_length=255,
    verbose_name=_("Number"),
  )
  status = models.CharField(
    max_length=25,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount_due = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Due"),
    help_text=_("Final amount due at this time"),
  )
  amount_paid = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Paid"),
    help_text=_("Amount that was paid by the customer, if any"),
  )
  amount_overpaid = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Overpaid"),
    help_text=_("Amount that was overpaid by the customer, if any"),
  )
  amount_remaining = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Remaining"),
    help_text=_("Amount remaining to be paid by the customer, if any"),
  )
  amount_shipping = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Shipping"),
    help_text=_("Shipping costs, if any"),
  )
  total_taxes_amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Tax"),
    help_text=_("Total applicable taxes"),
  )
  total_discounts_amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Discount"),
    help_text=_("Total discounts"),
  )
  subtotal_excluding_tax = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Subtotal excluding tax"),
    help_text=_("Total after item discounts and before taxes"),
  )
  subtotal = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Subtotal"),
    help_text=_("Total before discounts and taxes"),
  )
  total_excluding_tax = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Total excluding tax"),
    help_text=_("Total after all discounts and before taxes"),
  )
  total = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Total"),
    help_text=_("Total after discounts and taxes"),
  )
  period_start = models.DateTimeField(
    verbose_name=_("Period start"),
  )
  period_end = models.DateTimeField(
    verbose_name=_("Period end"),
  )
  collection_method = models.CharField(
    max_length=20,
    choices=CollectionMethod.choices,
    verbose_name=_("Collection method"),
    help_text=_(
      "Charge using the default source on file or email invoice with instructions"
    ),
  )
  invoice_pdf = models.URLField(
    max_length=255,
    verbose_name=_("Invoice PDF"),
  )
  hosted_invoice_url = models.URLField(
    max_length=255,
    verbose_name=_("Hosted invoice URL"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="invoices",
    verbose_name=_("Customer"),
  )
  subscription = models.ForeignKey(
    Subscription,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="invoices",
    blank=True,
    null=True,
    verbose_name=_("Subscription"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Invoice")
    verbose_name_plural = _("Invoices")
    get_latest_by = "created"

  def __str__(self) -> str:
    return self.number or self.id

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Invoice) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if stripe_obj.parent and stripe_obj.parent.subscription_details:
      stripe_sub = stripe_obj.parent.subscription_details.subscription
      if isinstance(stripe_sub, stripe.Subscription):
        data["subscription"] = stripe_sub
      elif isinstance(stripe_sub, str):
        data["subscription_id"] = stripe_sub

    divisor = CURRENCY_DIVISOR[stripe_obj.currency]
    data["total_discounts_amount"] = Decimal(0)
    for discount in stripe_obj.total_discount_amounts or []:
      data["total_discounts_amount"] -= Decimal(discount.amount / divisor)

    data["total_taxes_amount"] = Decimal(0)
    for tax in stripe_obj.total_taxes or []:
      data["total_taxes_amount"] += Decimal(tax.amount / divisor)

    return data

  @cached_property
  def has_prorations(self) -> bool:
    return self.lines.filter(proration=True).exists()


class InvoiceLineItem(StripeModel[stripe.StripeObject]):
  """Django implementation of Stripe Invoice Line Items.

  Notes
  -----

  Stripe Docs: https://docs.stripe.com/api/invoices/line_item

  """

  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
  )
  description = models.CharField(
    max_length=500,
    blank=True,
    verbose_name=_("Description"),
  )
  quantity = models.PositiveIntegerField(
    null=True,
    blank=True,
    verbose_name=_("Quantity"),
  )
  period_start = models.DateTimeField(
    verbose_name=_("Period start"),
  )
  period_end = models.DateTimeField(
    verbose_name=_("Period end"),
  )
  proration = models.BooleanField(
    default=False,
    verbose_name=_("Proration?"),
  )
  invoice = models.ForeignKey(
    Invoice,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="lines",
    verbose_name=_("Invoice"),
  )
  price = models.ForeignKey(
    Price,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    null=True,
    blank=True,
    related_name="invoice_line_items",
    verbose_name=_("Price"),
  )
  product = models.ForeignKey(
    Product,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    null=True,
    blank=True,
    related_name="invoice_line_items",
    verbose_name=_("Product"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Invoice Line Item")
    verbose_name_plural = _("Invoice Line Items")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.StripeObject) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if (period := stripe_obj.get("period")) is not None:
      data["period_start"] = dt.datetime.fromtimestamp(
        period["start"],
        tz=dt.UTC,
      )
      data["period_end"] = dt.datetime.fromtimestamp(
        period["end"],
        tz=dt.UTC,
      )

    if (pricing := stripe_obj.get("pricing")) is not None:
      if (price_details := pricing.get("price_details")) is not None:
        if (price_id := price_details.get("price")) is not None:
          data["price_id"] = price_id
        if (product_id := price_details.get("product")) is not None:
          data["product_id"] = product_id

    return data


class InvoicePayment(StripeModel[stripe.StripeObject]):
  """Django implementation of Stripe Invoice Payments.

  Notes
  -----

  Stripe Docs: https://docs.stripe.com/api/invoice-payment/object

  """

  API_EXPAND_FIELDS = (
    "invoice",
    "payment.payment_intent",
  )

  class Status(models.TextChoices):
    OPEN = "open", _("Open")
    PAID = "paid", _("Paid")
    CANCELED = "canceled", _("Canceled")

  created = models.DateTimeField(
    verbose_name=_("Created"),
  )
  status = models.CharField(
    max_length=10,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount_paid = MoneyField(
    max_digits=8,
    decimal_places=2,
    null=True,
    blank=True,
    verbose_name=_("Amount paid"),
    help_text=_("Null until the payment is paid"),
  )
  amount_requested = MoneyField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount requested"),
  )
  payment_intent = models.ForeignKey(
    PaymentIntent,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    null=True,
    blank=True,
    related_name="invoice_payments",
    verbose_name=_("Payment intent"),
  )
  invoice = models.ForeignKey(
    Invoice,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="payments",
    verbose_name=_("Invoice"),
  )
  is_default = models.BooleanField(
    verbose_name=_("Default?"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Invoice Payment")
    verbose_name_plural = _("Invoice Payments")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.StripeObject) -> dict[str, Any]:
    data = super().deserialize(stripe_obj)

    if (payment := stripe_obj.get("payment")) is not None:
      if payment.get("type") == "payment_intent":
        pi = payment.get("payment_intent")
        if isinstance(pi, stripe.PaymentIntent):
          data["payment_intent"] = pi
        elif isinstance(pi, str):
          data["payment_intent_id"] = pi

    return data


class BalanceTransaction(StripeModel[stripe.BalanceTransaction]):
  """Django implementation of Stripe Balance Transactions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/balance_transactions

  """

  class Type(models.TextChoices):
    ADJUSTMENT = "adjustment", _("Adjustment")
    ADVANCE = "advance", _("Advance")
    ADVANCE_FUNDING = "advance_funding", _("Advance funding")
    ANTICIPLATION_REPAYMENT = "anticipation_repayment", _("Anticipation repayment")
    APPLICATION_FEE = "application_fee", _("Application fee")
    APPLICATION_FEE_REFUND = "application_fee_refund", _("Application fee refund")
    CHARGE = "charge", _("Charge")
    CONNECT_COLLECTION_TRANSFER = (
      "connect_collection_transfer",
      _("Connect collection transfer"),
    )
    CONTRIBUTION = "contribution", _("Contribution")
    ISSUING_AUTHORIZATION_HOLD = (
      "issuing_authorization_hold",
      _("Issuing authorization hold"),
    )
    ISSUING_AUTHORIZATION_RELEASE = (
      "issuing_authorization_release",
      _("Issuing authorization release"),
    )
    ISSUING_DISPUTE = "issuing_dispute", _("Issuing dispute")
    ISSUING_TRANSACTION = "issuing_transaction", _("Issuing transaction")
    PAYMENT = "payment", _("Payment")
    PAYMENT_FAILURE_REFUND = "payment_failure_refund", _("Payment failure refund")
    PAYMENT_REFUND = "payment_refund", _("Payment refund")
    PAYOUT = "payout", _("Payout")
    PAYOUT_CANCEL = "payout_cancel", _("Payout cancel")
    PAYOUT_FAILURE = "payout_failure", _("Payout failure")
    REFUND = "refund", _("Refund")
    REFUND_FAILURE = "refund_failure", _("Refund failure")
    RESERVE_TRANSACTION = "reserve_transaction", _("Reserve transaction")
    RESERVED_FUNDS = "reserved_funds", _("Reserved funds")
    STRIPE_FEE = "stripe_fee", _("Stripe fee")
    STRIPE_FX_FEE = "stripe_fx_fee", _("Stripe currency conversion fee")
    TAX_FEE = "tax_fee", _("Taxes collected by Stripe")
    TOPUP = "topup", _("Topup")
    TOPUP_REVERSAL = "topup_reversal", _("Topup reversal")
    TRANSFER = "transfer", _("Transfer")
    TRANSFER_CANCEL = "transfer_cancel", _("Transfer cancelled")
    TRANSFER_FAILURE = "transfer_failure", _("Transfer failure")
    TRANSFER_REFUND = "transfer_refund", _("Transfer refund")

  class Status(models.TextChoices):
    AVAILABLE = "available", _("Available")
    PENDING = "pending", _("Pending")

  type = models.CharField(
    max_length=50,
    choices=Type.choices,
    verbose_name=_("Type"),
  )
  status = models.CharField(
    max_length=10,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  available_on = models.DateTimeField(
    verbose_name=_("Available on"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    is_balance=True,
    verbose_name=_("Amount"),
    help_text=_("Gross amount of this transaction"),
  )
  fee = MoneyField(
    max_digits=8,
    decimal_places=2,
    is_balance=True,
    verbose_name=_("Fee"),
    help_text=_("Fee paid for this transaction"),
  )
  net = MoneyField(
    max_digits=8,
    decimal_places=2,
    is_balance=True,
    verbose_name=_("Net"),
    help_text=_("Net amount of this transaction"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Balance Transaction")
    verbose_name_plural = _("Balance Transactions")
    ordering = ["-available_on", "-pk"]
    get_latest_by = "available_on"


class Charge(StripeModel[stripe.Charge]):
  """Django implementation of Stripe Charges.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/charges

  """

  API_EXPAND_FIELDS = (
    "customer",
    "payment_intent",
    "balance_transaction",
  )
  WEBHOOK_EVENTS = (
    "charge.captured",
    "charge.expired",
    "charge.failed",
    "charge.pending",
    "charge.refunded",
    "charge.succeeded",
    "charge.updated",
  )

  class Status(models.TextChoices):
    SUCCEEDED = "succeeded", _("Succeeded")
    PENDING = "pending", _("Pending")
    FAILED = "failed", _("Failed")

  customer = models.ForeignKey(
    Customer,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="charges",
    verbose_name=_("Customer"),
  )
  created = models.DateTimeField(
    verbose_name=_("Created"),
  )
  description = models.CharField(
    max_length=255,
    blank=True,
    verbose_name=_("Description"),
    help_text=_(
      "Optional description that may be used to display information to customers"
    ),
  )
  status = models.CharField(
    max_length=25,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    is_balance=True,
    verbose_name=_("Amount"),
    help_text=_("Total amount to be collected by this payment"),
  )
  receipt_email = models.EmailField(
    verbose_name=_("Receipt email"),
  )
  billing_details = models.JSONField(
    default=dict,
    verbose_name=_("Billing details"),
  )
  payment_intent = models.ForeignKey(
    PaymentIntent,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="charges",
    verbose_name=_("Payment intent"),
    blank=True,
    null=True,
  )
  balance_transaction = models.ForeignKey(
    BalanceTransaction,
    on_delete=models.PROTECT,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="charges",
    null=True,
    blank=True,
    verbose_name=_("Balance transaction"),
  )
  disputed = models.BooleanField(
    verbose_name=_("Disputed?"),
  )
  refunded = models.BooleanField(
    verbose_name=_("Refunded?"),
    help_text=_("Only 'True' if the Charge has been fully refunded"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Charge")
    verbose_name_plural = _("Charges")
    ordering = ["-created"]
    get_latest_by = "created"


class Refund(StripeModel[stripe.Refund]):
  """Django implementation of Stripe Refunds.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/refunds

  """

  API_EXPAND_FIELDS = (
    "charge.balance_transaction",
    "balance_transaction",
  )
  WEBHOOK_EVENTS = (
    "refund.created",
    "refund.failed",
    "refund.updated",
  )

  class Reason(models.TextChoices):
    DUPLICATE = "duplicate", _("Duplicate charge")
    FRAUDULENT = "fraudulent", _("Fraudulent charge")
    REQUESTED_BY_CUSTOMER = "requested_by_customer", _("Requested by customer")

  class Status(models.TextChoices):
    PENDING = "pending", _("Pending")
    SUCCEEDED = "succeeded", _("Succeeded")
    FAILED = "failed", _("Failed")

  reason = models.CharField(
    max_length=25,
    choices=Reason.choices,
    verbose_name=_("Reason"),
    help_text=_("Select reason for refund"),
  )
  status = models.CharField(
    max_length=25,
    choices=Status.choices,
    verbose_name=_("Status"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCY_CHOICES,
    verbose_name=_("Currency"),
  )
  amount = MoneyField(
    max_digits=8,
    decimal_places=2,
    is_balance=True,
    verbose_name=_("Amount"),
    help_text=_("Amount to be refunded"),
  )
  charge = models.ForeignKey(
    Charge,
    on_delete=models.CASCADE,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="refunds",
    verbose_name=_("Charge"),
    help_text=_("The charge to be refunded"),
  )
  balance_transaction = models.ForeignKey(
    BalanceTransaction,
    on_delete=models.SET_NULL,
    db_constraint=False,  # Stripe webhooks may arrive out of order
    related_name="refunds",
    blank=True,
    null=True,
    verbose_name=_("Balance transaction"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Refund")
    verbose_name_plural = _("Refunds")
