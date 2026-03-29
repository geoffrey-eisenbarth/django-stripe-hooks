import datetime as dt
from decimal import Decimal
from typing import (
  Type, TypeVar, Generic, TypeGuard, Protocol, runtime_checkable,
  Any, Self, Tuple, Iterable
)

import stripe

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _


T = TypeVar("T", bound=stripe.StripeObject)
Deserialized = Tuple[dict[str, Any], dict[str, Iterable[models.Model]]]


@runtime_checkable
class ManagedModel(Protocol):
  objects: models.Manager[models.Model]


def has_manager(val: Any) -> TypeGuard[Type[ManagedModel]]:
  return isinstance(val, type) and hasattr(val, "objects")


CURRENCIES = (
  ('', _("N/A")),
  ('usd', _("US Dollars")),
)


class StripeModel(models.Model, Generic[T]):
  """Common Stripe model methods and properties."""

  id = models.CharField(
    max_length=255,
    primary_key=True,
    verbose_name=_("Stripe ID"),
  )
  metadata = models.JSONField(
    default=dict,
    verbose_name=_("Metadata"),
    help_text=_(
      "Metadata for internal use only"
    ),
  )

  objects: models.Manager[Self]

  class Meta:
    abstract = True

  @classmethod
  def stripe_clean(
    cls,
    field: models.Field[Any, Any] | models.ForeignObjectRel,
    value: Any,
  ) -> Any:
    """Cleans a value from the Stripe API for a Django model."""
    if isinstance(field, models.CharField):
      if (value is None) and not field.null:
        value = ''
    elif isinstance(field, models.DateTimeField):
      value = dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    elif isinstance(field, models.IntegerField):
      if (value is None) and not field.null:
        value = 0
    elif isinstance(field, models.DecimalField):
      try:
        value = Decimal(value / 100)
      except TypeError:
        value = Decimal(0)
    elif isinstance(field, models.JSONField):
      value = getattr(value, 'data', value) or field.default()
    elif isinstance(field, (models.ForeignKey, models.OneToOneField)):
      value = getattr(value, 'id', value)

    elif isinstance(field, models.ManyToOneRel):
      # Related objects can't exist yet, get list of Stripe objects
      value = value.get('data')
    elif isinstance(field, models.ManyToManyField):
      # Related objects must already exist, get QuerySet of Django objects
      RelatedModel = field.related_model
      assert has_manager(RelatedModel)
      value = RelatedModel.objects.filter(
        id__in=[getattr(v, 'id', v) for v in value]
      )
    return value

  @classmethod
  def deserialize(cls, stripe_obj: T) -> Deserialized:
    """Convert Stripe object to model field values."""

    data, related_objs = {}, {}

    for field in cls._meta.get_fields():
      if field.name in stripe_obj:

        value = cls.stripe_clean(field, stripe_obj.get(field.name))

        if isinstance(field, (models.ManyToOneRel, models.ManyToManyField)):
          related_objs[field.name] = value
        elif isinstance(field, (models.ForeignKey, models.OneToOneField)):
          data[field.attname] = value
        else:
          data[field.name] = value

    return data, related_objs

  # TODO: Move to manager?
  @classmethod
  def from_stripe(cls, stripe_obj: T) -> Self:
    """Updates or creates a Django instance from a Stripe API object."""

    data, related_objs = cls.deserialize(stripe_obj)

    django_obj, created = cls.objects.update_or_create(
      id=data.pop('id'),
      defaults=data,
    )

    for field_name, objs in related_objs.items():
      field = cls._meta.get_field(field_name)
      if isinstance(field, models.ManyToManyField):
        getattr(django_obj, field_name).set(objs)
      elif isinstance(field, models.ManyToOneRel):
        RelatedModel = field.related_model
        assert has_manager(RelatedModel)
        assert issubclass(RelatedModel, StripeModel)
        for related_obj in objs:
          RelatedModel.from_stripe(related_obj)

    return django_obj


class Product(StripeModel[stripe.Product]):
  """Django implementation of Stripe Products.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/products

  """

  name = models.CharField(
    max_length=255,
    unique=True,  # Create index
    verbose_name=_("Name"),
    help_text=_(
      "The product's name, meant to be displayed to the customer"
    ),
  )
  description = models.TextField(
    verbose_name=_("Description"),
    help_text=_(
      "The product's description, meant to be displayed to the customer"
    ),
  )
  statement_descriptor = models.CharField(
    max_length=22,
    validators=[RegexValidator(
      regex=r'^[A-Za-z0-9]+$',
      message=_("Code must be alphanumeric"),
    )],
    verbose_name=_("Statement descriptor"),
    help_text=_(
      "This will appear on the customer's bank statement"
    ),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Product")
    verbose_name_plural = _("Products")
    ordering = ['pk']


class Price(StripeModel[stripe.Price]):
  """Django implementation of Stripe Prices.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/prices

  """

  TYPES = (
    ('recurring', _("Recurring charge")),
    ('one_time', _("One time charge")),
  )
  INTERVALS = (
    ('', _("N/A")),
    ('day', _("Daily")),
    ('week', _("Weekly")),
    ('month', _("Monthly")),
    ('year', _("Yearly")),
  )
  USAGE_TYPES = (
    ('', _("N/A")),
    ('licensed', _("Licensed usage")),
    ('metered', _("Metered usage")),
  )
  BILLING_SCHEMES = (
    ('per_unit', _("Per unit")),
    ('tiered', _("Tiered")),
  )
  TIERS_MODES = (
    ('na', _("N/A")),
    ('volume', _("Volume")),
    ('graduated', _("Graduated")),
  )

  active = models.BooleanField(
    verbose_name=_("Active?")
  )
  nickname = models.CharField(
    max_length=255,
    verbose_name=_("Nickname"),
    help_text=_(
      "A brief description of the plan visible to customers"
    ),
  )
  type = models.CharField(
    max_length=10,
    choices=TYPES,
    verbose_name=_("Type"),
    help_text=_(
      "One-time or recurring charge"
    ),
  )
  interval = models.CharField(
    max_length=10,
    choices=INTERVALS,
    blank=True,
    verbose_name=_("Interval"),
    help_text=_(
      "Required for recurring payments"
    ),
  )
  interval_count = models.PositiveIntegerField(
    null=True,
    verbose_name=_("Interval count"),
    help_text=_(
      "Number of intervals between subscription billings"
    ),
  )
  usage_type = models.CharField(
    max_length=10,
    blank=True,
    choices=USAGE_TYPES,
    verbose_name=_("Usage type"),
    help_text=(
      "Determines how the quantity per period should be determined"
    ),
  )
  billing_scheme = models.CharField(
    max_length=10,
    choices=BILLING_SCHEMES,
    verbose_name=_("Billing scheme"),
    help_text=(
      "How to compute the price per period"
    ),
  )
  tiers_mode = models.CharField(
    max_length=10,
    choices=TIERS_MODES,
    verbose_name=_("Tiers mode"),
    help_text=(
      "In volume-based tiering, the maximum quantity within a period determines the per unit price. "  # noqa: E501
      "In graduated tiering, the pricing can change as the quantity grows."
    ),
  )
  unit_amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Price"),
    help_text=_(
      "Amount to be charged (per interval for recurring payments)"
    ),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  product = models.ForeignKey(
    Product,
    on_delete=models.PROTECT,
    related_name='prices',
    verbose_name=_("Product"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Price")
    verbose_name_plural = _("Price")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Price) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)
    if (recurring := stripe_obj.get('recurring')) is not None:
      data.update({
        'interval': recurring.interval,
        'interval_count': recurring.interval_count,
        'usage_type': recurring.usage_type,
      })
    return data, related_objs


class PriceTier(models.Model):
  """Django implementation of a Price Tier for Stripe's Tiered Pricing.

  Notes
  -----

  Stripe Docs: https://docs.stripe.com/api/prices/object#price_object-tiers

  """

  flat_amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Flat amount"),
    help_text=_(
      "Base amount for the tier"
    ),
  )
  unit_amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Unit amount"),
    help_text=_(
      "Per unit cost in the tier"
    ),
  )
  up_to = models.PositiveIntegerField(
    blank=True,
    null=True,
    verbose_name=_("Up to"),
    help_text=_(
      "Quantity upper bound, leave blank for no upper bound"
    ),
  )
  price = models.ForeignKey(
    Price,
    on_delete=models.CASCADE,
    related_name='tiers',
    verbose_name=_("Price"),
  )

  @classmethod
  def deserialize(cls, stripe_obj: dict[str, Any]) -> Deserialized:
    return stripe_obj, {}


class Coupon(StripeModel[stripe.Coupon]):
  """Django implementation of Stripe Coupons.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/coupons

  """

  DURATIONS = (
    ('once', _("Once")),
    ('forever', _("Forever")),
  )

  name = models.CharField(
    max_length=255,
    blank=False,
    verbose_name=_("Name"),
    help_text=_(
      "Name to display on invoices"
    ),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  percent_off = models.PositiveIntegerField(
    default=0,
    verbose_name=_("Percent off"),
    help_text=_(
      "Percent that will be taken off of a subscription"
    ),
  )
  amount_off = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount off"),
    help_text=_(
      "Amount that will be taken off of a subscription"
    ),
  )
  duration = models.CharField(
    max_length=9,
    choices=DURATIONS,
    verbose_name=_("Duration"),
    help_text=_(
      "How long the discount will apply to a customer's subscription"
    ),
  )
  products = models.ManyToManyField(
    Product,
    related_name='coupons',
    blank=True,
    verbose_name=_("Products"),
    help_text=_(
      "Specify which product(s) this coupon will apply to"
    ),
  )
  max_redemptions = models.PositiveIntegerField(
    default=0,
    verbose_name=_("Maximum number of redemptions"),
    help_text=_(
      "Optional, leave blank for infinite redemptions"
    ),
  )
  times_redeemed = models.PositiveIntegerField(
    default=0,
    editable=False,
    verbose_name=_("Current number of redemptions"),
  )

  @property
  def terms(self) -> str:
    if self.percent_off:
      terms = f'{self.percent_off}% off {self.duration}'
    elif self.amount_off:
      terms = f'${self.amount_off} off {self.duration}'
    return terms

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Coupon) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)
    if (applies_to := stripe_obj.get('applies_to')) is not None:
      assert has_manager(Product)
      related_objs['products'] = Product.objects.filter(
        id__in=applies_to.products,
      )
    return data, related_objs


class PromotionCode(StripeModel[stripe.PromotionCode]):
  """Django implementation of Stripe Promotion Codes.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/promotion_codes

  """

  active = models.BooleanField(
    verbose_name=_("Active?")
  )
  code = models.CharField(
    max_length=100,
    unique=True,
    validators=[RegexValidator(
      regex=r'^[A-Za-z0-9]+$',
      message=_("Code must be alphanumeric"),
    )],
    verbose_name=_("Code"),
    help_text=_(
      "Alphanumeric code with no spaces or symbols"
    ),
  )
  expires_at = models.DateField(
    blank=True,
    null=True,
    verbose_name=_("Expires on"),
    help_text=_(
      "Optional, leave blank for no expiration date"
    ),
  )
  max_redemptions = models.PositiveIntegerField(
    default=0,
    verbose_name=_("Maximum number of redemptions"),
    help_text=_(
      "Optional, leave blank for infinite redemptions"
    ),
  )
  times_redeemed = models.PositiveIntegerField(
    default=0,
    editable=False,
    verbose_name=_("Current number of redemptions"),
  )
  coupon = models.ForeignKey(
    Coupon,
    on_delete=models.CASCADE,
    related_name='promotion_codes',
    verbose_name=_("Coupon"),
  )

  @property
  def redemptions(self) -> str:
    count = f'{self.times_redeemed:,d}'
    if self.max_redemptions:
      maximum = f'{self.max_redemptions:,d}'
    else:
      maximum = '&infin;'
    return mark_safe(f"{count} of {maximum}")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.PromotionCode) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)
    if stripe_obj.promotion:
      if (stripe_coupon := stripe_obj.promotion.coupon):
        data['coupon_id'] = getattr(stripe_coupon, 'id', stripe_coupon)
    return data, related_objs

  class Meta(StripeModel.Meta):
    verbose_name = _("Promotion Code")
    verbose_name_plural = _("Promotion Codes")

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


class Customer(StripeModel[stripe.Customer]):
  """Django implementation of Stripe Customers.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/customers

  """

  email = models.EmailField(
    unique=True,
    verbose_name=_("Email address"),
  )
  name = models.CharField(
    max_length=255,
    blank=True,
    verbose_name=_("Name"),
  )
  phone = models.CharField(
    max_length=20,
    blank=True,
    verbose_name=_("Phone number"),
  )


class PaymentMethod(StripeModel[stripe.PaymentMethod]):
  """Django implementation of Stripe PaymentMethods.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/payment_methods

  """

  TYPES = (
    ('card', _("Credit/Debit Card")),
    ('afterpay_clearpay', _("Afterpay/Clearpay")),
    ('alipay', _("Alipay (China)")),
    ('au_becs_debit', _("BECS Debit (Australia)")),
    ('bacs_debit', _("BACS Debit (United Kingdom)")),
    ('bancontact', _("Bancontact (Belgium)")),
    ('eps', _("EPS (Austria)")),
    ('fpx', _("FPX (Malaysia)")),
    ('giropay', _("giropay (Germany)")),
    ('grabpay', _("GrabPay (Southeast Asia)")),
    ('ideal', _("iDEAL (Netherlands)")),
    ('interac_preset', _("Interac (Stripe Terminal)")),
    ('p24', _("Przelewy24 (Poland)")),
    ('sepa_debit', _("SEPA Direct Debit (European Union)")),
    ('sofort', _("Sofort (Europe)")),
  )

  CARD_BRANDS = (
    ('amex', _("American Express")),
    ('cartes_bancaires', _("Cartes Bancaires")),
    ('diners', _("Diners Club")),
    ('discover', _("Discover")),
    ('jcb', _("JCB")),
    ('mastercard', _("MasterCard")),
    ('visa', _("Visa")),
    ('unionpay', _("UnionPay")),
    ('unknown', _("Unknown")),
  )

  is_attached = models.BooleanField(
    verbose_name=_("Attached?"),
  )
  type = models.CharField(
    max_length=17,
    choices=TYPES,
    verbose_name=_("Type"),
  )
  card_brand = models.CharField(
    max_length=16,
    choices=CARD_BRANDS,
    verbose_name=_("Card brand"),
  )
  card_exp_month = models.IntegerField(
    verbose_name=_("Two-digit card expiration month"),
  )
  card_exp_year = models.IntegerField(
    verbose_name=_("Four-digit card expiration year"),
  )
  card_last4 = models.CharField(
    max_length=4,
    verbose_name=_("Card last four"),
  )
  zip_code = models.CharField(
    max_length=10,
    blank=True,
    validators=[RegexValidator(
      regex=r'(^\d{5}$)|(^\d{9}$)|(^\d{5}-\d{4}$)',
      message=_("ZIP Coode must be 5 or 9 digits"),
    )],
    verbose_name=_("ZIP code"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    related_name='payment_methods',
    verbose_name=_("Customer"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Payment Method")
    verbose_name_plural = _("Payment Methods")
    ordering = ['-card_exp_year', '-card_exp_month']

  @property
  def card_info(self) -> str:
    s = "{brand} {bullets} {bullets} {bullets} {last4}".format(
      brand=dict(self.CARD_BRANDS)[self.card_brand],
      bullets='\u2022' * 4,
      last4=self.card_last4,
    )
    return s

  @classmethod
  def deserialize(cls, stripe_obj: stripe.PaymentMethod) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)

    data['is_attached'] = bool(stripe_obj.customer)

    if stripe_obj.billing_details.address is not None:
      data['zip_code'] = stripe_obj.billing_details.address.postal_code or ''

    if (card := stripe_obj.get('card')) is not None:
      data.update({
        'card_brand': card.brand,
        'card_last4': card.last4,
        'card_exp_month': card.exp_month,
        'card_exp_year': card.exp_year,
      })
    return data, related_objs


class PaymentIntent(StripeModel[stripe.PaymentIntent]):
  """Django implementation of Stripe Payment Intents.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/payment_intents

  """

  FUTURE_USAGES = (
    ('on_session', _("On Session")),
    ('off_session', _("Off Session")),
  )

  STATUSES = (
    ('requires_payment_method', _("Requires payment method")),
    ('requires_confirmation', _("Requires confirmation")),
    ('requires_action', _("Requires action")),
    ('processing', _("Processing")),
    ('requires_capture', _("Requires capture")),
    ('canceled', _("Canceled")),
    ('succeeded', _("Succeeded")),
  )

  amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
    help_text=_(
      "Amount to be collected"
    ),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  description = models.CharField(
    max_length=255,
    verbose_name=_("Description"),
    help_text=_(
      "Description of this charge that will be displayed to customers"
    ),
  )
  setup_future_usage = models.CharField(
    max_length=11,
    choices=FUTURE_USAGES,
    verbose_name=_("Setup future usage"),
  )
  status = models.CharField(
    max_length=23,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    related_name='payment_intents',
    verbose_name=_("Customer"),
  )
  receipt_email = models.EmailField(
    verbose_name=_("Email address"),
  )
  payment_method = models.ForeignKey(
    PaymentMethod,
    on_delete=models.SET_NULL,
    related_name='payment_intents',
    blank=True,
    null=True,
    verbose_name=_("Payment method"),
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
  card_brand = models.CharField(
    max_length=16,
    choices=PaymentMethod.CARD_BRANDS,
    verbose_name=_("Card brand"),
    blank=True,
  )
  card_exp_month = models.IntegerField(
    verbose_name=_("Two-digit card expiration month"),
  )
  card_exp_year = models.IntegerField(
    verbose_name=_("Four-digit card expiration year"),
  )
  card_last4 = models.CharField(
    max_length=4,
    verbose_name=_("Card last four"),
    blank=True,
  )
  zip_code = models.CharField(
    max_length=10,
    blank=True,
    validators=[RegexValidator(
      regex=r'(^\d{5}$)|(^\d{9}$)|(^\d{5}-\d{4}$)',
      message=_("ZIP Coode must be 5 or 9 digits"),
    )],
    verbose_name=_("ZIP code"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    related_name='confirmation_tokens',
    verbose_name=_("Customer"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Confirmation Token")
    verbose_name_plural = _("Confirmation Tokens")
    ordering = ['customer', '-created']
    get_latest_by = 'created'

  @property
  def is_expired(self) -> bool:
    return (self.expires_at < timezone.now())

  @property
  def card_info(self) -> str:
    s = "{brand} {bullets} {bullets} {bullets} {last4}".format(
      brand=dict(PaymentMethod.CARD_BRANDS)[self.card_brand],
      bullets='\u2022' * 4,
      last4=self.card_last4,
    )
    return s


class FundingInstructions(models.Model):
  """Django implementation of Stripe Funding Instructions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/issuing/funding_instructions

  """

  customer = models.OneToOneField(
    Customer,
    on_delete=models.CASCADE,
    related_name='funding_instructions',
    verbose_name=_("Customer"),
  )
  account_holder_address = models.JSONField(
    default=dict,
    verbose_name=_("Account holder address"),
  )
  account_holder_name = models.CharField(
    max_length=255,
    verbose_name=_("Account holder name"),
  )
  account_number = models.CharField(
    max_length=17,
    verbose_name=_("Account number"),
  )
  account_type = models.CharField(
    max_length=10,
    verbose_name=_("Account type"),
  )
  bank_address = models.JSONField(
    default=dict,
    verbose_name=_("Bank address"),
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

  class Meta(StripeModel.Meta):
    verbose_name = _("Funding Instructions")
    verbose_name_plural = _("Funding Instructions")

  @classmethod
  def deserialize(cls, stripe_obj: stripe.FundingInstructions) -> Deserialized:
    data = {}
    for fa in stripe_obj.bank_transfer.financial_addresses:
      if (fa.type == 'aba') and (fa.aba is not None):
        data.update({
          'account_holder_address': dict(fa.aba.account_holder_address),
          'account_holder_name': fa.aba.account_holder_name,
          'account_number': fa.aba.account_number,
          'account_type': fa.aba.account_type,
          'bank_address': dict(fa.aba.bank_address),
          'bank_name': fa.aba.bank_name,
          'routing_number': fa.aba.routing_number,
        })
      elif (fa.type == 'swift') and (fa.swift is not None):
        data['swift_code'] = fa.swift.swift_code

    return data, {}

  @classmethod
  def from_stripe(
    cls,
    customer: Customer,
    stripe_obj: stripe.FundingInstructions,
  ) -> models.Model:
    """Returns a Django model instance based on Stripe API object."""
    data, related_objs = FundingInstructions.deserialize(stripe_obj)
    assert has_manager(cls)
    django_obj, created = cls.objects.update_or_create(
      customer=customer,
      defaults=data,
    )
    return django_obj


class Subscription(StripeModel[stripe.Subscription]):
  """Django implementation of Stripe Subscriptions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/subscriptions

  """

  STATUSES = (
    ('incomplete', _("Incomplete")),
    ('incomplete_expired', _("Incomplete expired")),  # Terminal state
    ('trialing', _("Trial")),
    ('active', _("Active")),
    ('past_due', _("Past due")),
    ('canceled', _("Canceled")),
    ('unpaid', _("Unpaid")),
  )
  ACTIVE_STATUSES = (
    'trialing',
    'active',
  )
  TERMINAL_STATUSES = (
    'incomplete_expired',
    'canceled',
  )
  COLLECTION_METHODS = (
    ('charge_automatically', _("Charge automatically")),
    ('send_invoice', _("Send invoice")),
  )

  status = models.CharField(
    max_length=18,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  cancel_at_period_end = models.BooleanField(
    verbose_name=_("Cancel at period end?"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.PROTECT,
    related_name='subscriptions',
    verbose_name=_("Customer"),
  )
  promotion_code = models.ForeignKey(
    PromotionCode,
    on_delete=models.PROTECT,
    related_name='subscriptions',
    blank=True,
    null=True,
    verbose_name=_("Promotion code"),
  )
  default_payment_method = models.ForeignKey(
    PaymentMethod,
    on_delete=models.PROTECT,
    related_name='subscriptions',
    blank=True,
    null=True,
    verbose_name=_("Default payment method"),
  )
  collection_method = models.CharField(
    max_length=20,
    choices=COLLECTION_METHODS,
    verbose_name=_("Collection method"),
    help_text=_(
      "Charge using the default source on file or email invoice with instructions"  # noqa: E501
    ),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Subscription")
    verbose_name_plural = _("Subscriptions")
    get_latest_by = 'current_period_start'

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Subscription) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)

    # Add PromotionCode
    stripe_discount = getattr(stripe_obj, "discount", None)
    stripe_invoice = getattr(stripe_obj, "latest_invoice", None)
    if d := (stripe_discount or getattr(stripe_invoice, "discount", None)):
      data['promotion_code_id'] = d.promotion_code

    # Add SubscriptionItems (must be a QuerySet)
    assert has_manager(SubscriptionItem)
    related_objs['items'] = SubscriptionItem.objects.filter(
      id__in=[item.id for item in stripe_obj['items'].data]
    )

    return data, related_objs

  @cached_property
  def current_period_start(self) -> dt.datetime:
    return min(item.current_period_start for item in self.items.all())

  @cached_property
  def current_period_end(self) -> dt.datetime:
    return max(item.current_period_start for item in self.items.all())


class SubscriptionItem(StripeModel[stripe.SubscriptionItem]):
  """Django implementation of Stripe Subscription Items.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/subscription_items

  """

  price = models.ForeignKey(
    Price,
    on_delete=models.PROTECT,
    related_name='subscription_items',
    verbose_name=_("Price"),
  )
  quantity = models.PositiveIntegerField(
    verbose_name=_("Quantity"),
  )
  current_period_start = models.DateTimeField(
    verbose_name=_("Current period start"),
  )
  current_period_end = models.DateTimeField(
    verbose_name=_("Current period end"),
  )
  subscription = models.ForeignKey(
    Subscription,
    on_delete=models.CASCADE,
    related_name='items',
    verbose_name=_("Subscription"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Subscription Item")
    verbose_name_plural = _("Subscription Items")

    constraints = [
      models.UniqueConstraint(
        fields=['price', 'subscription'],
        name='unique_item',
      ),
    ]


class Invoice(StripeModel[stripe.Invoice]):
  """Django implementation of Stripe Invoices.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/invoices

  """

  COLLECTION_METHODS = (
    ('charge_automatically', _("Charge automatically")),
    ('send_invoice', _("Send invoice")),
  )
  STATUSES = (
    ('draft', _("Draft")),
    ('open', _("Open")),
    ('paid', _("Paid")),
    ('uncollectible', _("Uncollectible")),
    ('void', _("Void")),
  )

  number = models.CharField(
    max_length=255,
    verbose_name=_("Number"),
  )
  auto_advance = models.BooleanField(
    verbose_name=_("Auto advance?"),
    help_text=_(
      "Controls whether Stripe will perform automatic collection of the invoice"  # noqa: E501
    ),
  )
  collection_method = models.CharField(
    max_length=20,
    choices=COLLECTION_METHODS,
    verbose_name=_("Collection method"),
    help_text=_(
      "Charge using the default source on file or email invoice with instructions"  # noqa: E501
    ),
  )
  status = models.CharField(
    max_length=25,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  lines = models.JSONField(
    default=list,
    verbose_name=_("Lines"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  subtotal = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Subtotal"),
    help_text=_(
      "Total before discounts and taxes"
    ),
  )
  tax = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Tax"),
    help_text=_(
      "Total applicable taxes"
    ),
  )
  discount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Discount"),
    help_text=_(
      "Total discounts"
    ),
  )
  total = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Total"),
    help_text=_(
      "Total after discounts and taxes"
    ),
  )
  amount_due = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount Due"),
    help_text=_(
      "Final amount due at this time"
    ),
  )
  period_start = models.DateTimeField(
    verbose_name=_("Created"),
  )
  period_end = models.DateTimeField(
    verbose_name=_("Finalized"),
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
    on_delete=models.CASCADE,
    related_name='invoices',
    verbose_name=_("Customer"),
  )
  subscription = models.ForeignKey(
    Subscription,
    on_delete=models.CASCADE,
    related_name='invoices',
    blank=True,
    null=True,
    verbose_name=_("Subscription"),
  )
  payment_intent = models.OneToOneField(
    PaymentIntent,
    on_delete=models.SET_NULL,
    related_name='invoice',
    blank=True,
    null=True,
    verbose_name=_("Payment intent"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Invoice")
    verbose_name_plural = _("Invoices")
    get_latest_by = 'period_start'

  @cached_property
  def has_prorations(self) -> bool:
    if self.lines:
      has_prorations = any(
        line.get('proration', False)
        for line in self.lines
      )
    else:
      has_prorations = False
    return has_prorations

  @classmethod
  def deserialize(cls, stripe_obj: stripe.Invoice) -> Deserialized:
    data, related_objs = super().deserialize(stripe_obj)

    if stripe_obj.parent and stripe_obj.parent.subscription_details:
      if (stripe_sub := stripe_obj.parent.subscription_details.subscription):
        data['subscription_id'] = getattr(stripe_sub, 'id', stripe_sub)

    data['discount'] = Decimal(0)
    for discount in (stripe_obj.total_discount_amounts or []):
      data['discount'] -= Decimal(discount.amount / 100)

    data['tax'] = Decimal(0)
    for tax in (stripe_obj.total_taxes or []):
      data['tax'] += Decimal(tax.amount / 100)

    return data, related_objs


class BalanceTransaction(StripeModel[stripe.BalanceTransaction]):
  """Django implementation of Stripe Balance Transactions.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/balance_transactions

  """

  STATUSES = (
    ('available', _("Available")),
    ('pending', _("Pending")),
  )
  TYPES = (
    ('adjustment', _("Adjustment")),
    ('advance', _("Advance")),
    ('advance_funding', _("Advance funding")),
    ('anticipation_repayment', _("Anticipation repayment")),
    ('application_fee', _("Application fee")),
    ('application_fee_refund', _("Application fee refund")),
    ('charge', _("Charge")),
    ('connect_collection_transfer', _("Connect collection transfer")),
    ('contribution', _("Contribution")),
    ('issuing_authorization_hold', _("Issuing authorization hold")),
    ('issuing_authorization_release', _("Issuing authorization release")),
    ('issuing_dispute', _("Issuing dispute")),
    ('issuing_transaction', _("Issuing transaction")),
    ('payment', _("Payment")),
    ('payment_failure_refund', _("Payment failure refund")),
    ('payment_refund', _("Payment refund")),
    ('payout', _("Payout")),
    ('payout_cancel', _("Payout cancel")),
    ('payout_failure', _("Payout failure")),
    ('refund', _("Refund")),
    ('refund_failure', _("Refund failure")),
    ('reserve_transaction', _("Reserve transaction")),
    ('reserved_funds', _("Reserved funds")),
    ('stripe_fee', _("Stripe fee")),
    ('stripe_fx_fee', _("Stripe currency conversion fee")),
    ('tax_fee', _("Taxes collected by Stripe")),
    ('topup', _("Topup")),
    ('topup_reversal', _("Topup reversal")),
    ('transfer', _("Transfer")),
    ('transfer_cancel', _("Transfer cancelled")),
    ('transfer_failure', _("Transfer failure")),
    ('transfer_refund', _("Transfer refund")),
  )

  amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
    help_text=_(
      "Gross amount of this "
      "transaction"
    ),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  fee = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Fee"),
    help_text=_(
      "Fee paid for this transaction"
    ),
  )
  net = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Net"),
    help_text=_(
      "Net amount of this transaction"
    ),
  )
  status = models.CharField(
    max_length=10,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  type = models.CharField(
    max_length=50,
    choices=TYPES,
    verbose_name=_("Type"),
  )
  available_on = models.DateTimeField(
    verbose_name=_("Available on"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Balance Transaction")
    verbose_name_plural = _("Balance Transactions")
    ordering = ['-available_on', '-pk']
    get_latest_by = 'available_on'


class Charge(StripeModel[stripe.Charge]):
  """Django implementation of Stripe Charges.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/charges

  """

  STATUSES = (
    ('succeeded', _("Succeeded")),
    ('pending', _("Pending")),
    ('failed', _("Failed")),
  )

  amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
    help_text=_(
      "Total amount to be collected by this payment"
    ),
  )
  created = models.DateTimeField(
    verbose_name=_("Created"),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  description = models.CharField(
    max_length=255,
    blank=True,
    verbose_name=_("Description"),
    help_text=_(
      "Optional description that may be used to display information to customers"  # noqa: E501
    ),
  )
  disputed = models.BooleanField(
    verbose_name=_("Disputed?"),
  )
  refunded = models.BooleanField(
    verbose_name=_("Refunded?"),
    help_text=_(
      "Only 'True' if the Charge has been fully refunded"
    ),
  )
  status = models.CharField(
    max_length=25,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  customer = models.ForeignKey(
    Customer,
    on_delete=models.CASCADE,
    related_name='charges',
    verbose_name=_("Customer"),
  )
  payment_intent = models.ForeignKey(
    PaymentIntent,
    on_delete=models.CASCADE,
    related_name='charges',
    verbose_name=_("Payment intent"),
  )
  balance_transaction = models.ForeignKey(
    BalanceTransaction,
    on_delete=models.CASCADE,
    related_name='charges',
    verbose_name=_("Balance transaction"),
  )
  invoice = models.ForeignKey(
    Invoice,
    on_delete=models.CASCADE,
    related_name='charges',
    verbose_name=_("Invoice"),
  )
  receipt_email = models.EmailField(
    verbose_name=_("Receipt email"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Charge")
    verbose_name_plural = _("Charges")
    ordering = ['-created']
    get_latest_by = 'created'


class Refund(StripeModel[stripe.Refund]):
  """Django implementation of Stripe Refunds.

  Notes
  -----

  Stripe Docs: https://stripe.com/docs/api/refunds

  """

  REASONS = (
    ('duplicate', _("Duplicate charge")),
    ('fraudulent', _("Fraudulent charge")),
    ('requested_by_customer', _("Requested by customer")),
  )
  STATUSES = (
    ('pending', _("Pending")),
    ('succeeded', _("Succeeded")),
    ('failed', _("Failed")),
  )

  amount = models.DecimalField(
    max_digits=8,
    decimal_places=2,
    verbose_name=_("Amount"),
    help_text=_(
      "Amount to be refunded"
    ),
  )
  currency = models.CharField(
    max_length=3,
    choices=CURRENCIES,
    verbose_name=_("Currency"),
  )
  reason = models.CharField(
    max_length=25,
    choices=REASONS,
    verbose_name=_("Reason"),
    help_text=_(
      "Select reason for refund"
    ),
  )
  status = models.CharField(
    max_length=25,
    choices=STATUSES,
    verbose_name=_("Status"),
  )
  charge = models.ForeignKey(
    Charge,
    on_delete=models.CASCADE,
    related_name='refunds',
    verbose_name=_("Charge"),
    help_text=_(
      "The charge to be refunded"
    ),
  )
  balance_transaction = models.ForeignKey(
    BalanceTransaction,
    on_delete=models.SET_NULL,
    related_name='refunds',
    blank=True,
    null=True,
    verbose_name=_("Balance transaction"),
  )

  class Meta(StripeModel.Meta):
    verbose_name = _("Refund")
    verbose_name_plural = _("Refunds")
