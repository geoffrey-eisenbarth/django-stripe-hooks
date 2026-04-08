import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
import stripe

from django.conf import settings

from django_stripe_hooks.managers import allow_stripe_write
from django_stripe_hooks.models import (
  Customer, ConfirmationToken, FundingInstructions,
  PaymentMethod,
  Product, PriceTier, Coupon, PromotionCode, Discount,
  Invoice, InvoicePayment,
)


@pytest.mark.django_db(transaction=True)
class TestSpecializedModels:

  @pytest.fixture(autouse=True)
  def setup_stripe(self) -> None:
    self.stripe_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

  def test_confirmation_tokens(self) -> None:
    s_customer = self.stripe_client.v1.customers.create(params={
      'email': 'token@test.com',
      'name': 'ConfirmationToken Test',
    })
    d_customer = Customer.objects.from_stripe(s_customer)

    s_token = self.stripe_client.v1.test_helpers.confirmation_tokens.create(
      params={'payment_method': 'pm_card_visa'},
    )

    data: dict[str, Any] = {}
    for field_name in ['created', 'expires_at']:
      if value := getattr(s_token, field_name):
        data[field_name] = dt.datetime.fromtimestamp(value, dt.UTC)

    if (pm := s_token.payment_method_preview) is not None:
      data['payment_method_preview'] = dict(pm)
      if (card := pm.card) is not None:
        data.update({
          'card_brand': card.brand,
          'card_exp_month': card.exp_month,
          'card_exp_year': card.exp_year,
          'card_last4': card.last4,
        })
      if (billing_details := pm.billing_details) is not None:
        if (address := billing_details.address) is not None:
          data['zip_code'] = address.postal_code or ''

    ConfirmationToken.objects.create(customer=d_customer, **data)

  def test_funding_instructions_logic(self) -> None:
    s_customer = self.stripe_client.v1.customers.create(params={
      'email': 'funding@test.com',
      'name': 'FundingInstructions Test',
    })
    d_customer = Customer.objects.from_stripe(s_customer)

    d_fi = FundingInstructions.objects.from_stripe(
      d_customer,
      bank_transfer_type='us_bank_transfer',
      currency='usd',
    )

    assert d_fi.customer.id == d_customer.id


@pytest.mark.django_db(transaction=True)
class TestWriteGuard:
  """Ensure StripeModel.save() and .delete() reject direct writes."""

  @pytest.fixture()
  def customer(self) -> Customer:
    """Create a Customer bypassing the guard for testing on."""
    with allow_stripe_write():
      return Customer.objects.create(
        id='cus_test_guard',
        email='guard@test.com',
      )

  def test_save_raises_without_context(
    self,
    customer: Customer,
  ) -> None:
    customer.email = 'changed@test.com'
    with pytest.raises(TypeError, match='managed by Stripe'):
      customer.save()

  def test_create_raises_without_context(self) -> None:
    with pytest.raises(TypeError, match='managed by Stripe'):
      Customer.objects.create(id='cus_test_create', email='create@test.com')

  def test_delete_raises_without_context(
    self,
    customer: Customer,
  ) -> None:
    with pytest.raises(TypeError, match='managed by Stripe'):
      customer.delete()

  def test_save_permitted_inside_context(
    self,
    customer: Customer,
  ) -> None:
    customer.email = 'updated@test.com'
    with allow_stripe_write():
      customer.save()
    customer.refresh_from_db()
    assert customer.email == 'updated@test.com'

  def test_delete_permitted_inside_context(
    self,
    customer: Customer,
  ) -> None:
    with allow_stripe_write():
      customer.delete()
    assert not Customer.objects.filter(id='cus_test_guard').exists()

  def test_nested_context_does_not_revoke_access(
    self,
    customer: Customer,
  ) -> None:
    """Exiting an inner allow_stripe_write must not block the outer one."""
    customer.email = 'outer@test.com'
    with allow_stripe_write():
      with allow_stripe_write():
        pass  # inner exits here — counter goes to 1, not 0
      customer.save()  # must still work
    customer.refresh_from_db()
    assert customer.email == 'outer@test.com'

  def test_guard_restored_after_context_exits(
    self,
    customer: Customer,
  ) -> None:
    """Writes should be blocked again once the context manager exits."""
    with allow_stripe_write():
      pass
    customer.email = 'blocked@test.com'
    with pytest.raises(TypeError, match='managed by Stripe'):
      customer.save()


@pytest.mark.django_db
class TestFromStripe:
  """Unit tests for StripeManager.from_stripe() edge-case paths."""

  def test_m2o_rel_inline_children(self) -> None:
    """ManyToOneRel post-save: inline child StripeObjects are saved.

    Covers the ManyToOneRel branch in from_stripe() along with the bare-string
    ID skip that handles out-of-order webhook references.
    """

    s_price = stripe.Price.construct_from({
      'id': 'price_inline',
      'object': 'price',
      'active': True,
      'nickname': 'Inline Price',
      'type': 'recurring',
      'interval': 'month',
      'billing_scheme': 'per_unit',
      'tiers_mode': 'na',
      'unit_amount': 1000,
      'currency': 'usd',
      'product': 'prod_inline',
    }, 'key')

    s_product = stripe.Product.construct_from({
      'id': 'prod_inline',
      'object': 'product',
      'active': True,
      'name': 'Inline Product',
      'description': 'Has inline prices',
      'statement_descriptor': 'INLINE',
      # A real StripeObject child plus a bare ID (bare ID should be skipped)
      'prices': [s_price, 'price_other'],
    }, 'key')

    from django_stripe_hooks.models import Product, Price
    django_product = Product.objects.from_stripe(s_product)

    assert Product.objects.filter(id='prod_inline').exists()
    assert Price.objects.filter(
      id='price_inline',
      product=django_product,
    ).exists()


@pytest.mark.django_db
class TestModelProperties:
  """Test computed properties on models that don't require the Stripe API."""

  @pytest.fixture(autouse=True)
  def create_fixtures(self) -> None:
    with allow_stripe_write():
      customer = Customer.objects.create(
        id='cus_prop_test',
        email='props@test.com',
      )
      PaymentMethod.objects.create(
        id='pm_card',
        type='card',
        card={'brand': 'visa', 'last4': '4242'},
        customer=customer,
      )
      PaymentMethod.objects.create(
        id='pm_empty',
        type='card',
        card={},
        customer=customer,
      )
      now = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
      ConfirmationToken.objects.create(
        id='ct_test',
        created=now,
        expires_at=now - dt.timedelta(days=1),
        card_brand='visa',
        card_exp_month=12,
        card_exp_year=2030,
        card_last4='4242',
        zip_code='',
        customer=customer,
      )

  def test_confirmation_token_properties(self) -> None:
    """ConfirmationToken.is_expired and card_info properties."""

    token = ConfirmationToken.objects.get(id='ct_test')

    assert token.is_expired is True

    info = token.card_info
    assert 'Visa' in info
    assert '4242' in info

  def test_payment_method_card_info(self) -> None:
    """PaymentMethod.card_info with and without card data."""

    pm_card = PaymentMethod.objects.get(id='pm_card')
    info = pm_card.card_info
    assert 'Visa' in info
    assert '4242' in info

    pm_empty = PaymentMethod.objects.get(id='pm_empty')
    assert pm_empty.card_info == ''


class TestDeserializeEdgeCases:
  """Unit-test edge-case branches in StripeModel.deserialize() overrides."""

  def test_m2o_rel_with_dict_value(self) -> None:
    """ManyToOneRel field: value is a plain dict with 'data' key → line 133.

    This branch is reached when the caller passes a raw dict rather than a
    StripeObject, which happens during manual deserialization. construct_from()
    cannot reproduce it because nested dicts are eagerly converted to
    StripeObjects (which expose .data directly, skipping the dict branch).
    """

    result = Product.deserialize({  # type: ignore[arg-type]
      'prices': {'data': ['price_test']}
    })
    assert result.get('prices') == ['price_test']

  def test_m2m_field(self) -> None:
    """ManyToManyField: value is a list of IDs → line 137."""

    stripe_obj = stripe.Coupon.construct_from({
      'products': ['prod_test'],
    }, 'key')
    result = Coupon.deserialize(stripe_obj)
    assert result.get('products') == ['prod_test']

  def test_price_tier_deserialize(self) -> None:
    """PriceTier.deserialize returns dict(stripe_obj) → line 382."""

    stripe_obj = stripe.StripeObject.construct_from({
      'flat_amount': 100,
      'unit_amount': 5,
    }, 'key')
    result = PriceTier.deserialize(stripe_obj)
    assert result == {'flat_amount': 100, 'unit_amount': 5}

  def test_promotion_code_deserialize_str_coupon(self) -> None:
    """PromotionCode.deserialize with a string coupon ID → lines 570-571."""

    stripe_obj = stripe.PromotionCode.construct_from({
      'promotion': {'coupon': 'coup_test'},
    }, 'key')
    result = PromotionCode.deserialize(stripe_obj)
    assert result.get('coupon_id') == 'coup_test'

  def test_discount_deserialize_str_coupon(self) -> None:
    """Discount.deserialize with a string coupon ID → line 692."""

    stripe_obj = stripe.Discount.construct_from({
      'source': {'coupon': 'coup_test'},
    }, 'key')
    result = Discount.deserialize(stripe_obj)
    assert result.get('coupon_id') == 'coup_test'

  def test_discount_deserialize_coupon_object(self) -> None:
    """Discount.deserialize with a stripe.Coupon object → line 690."""

    stripe_obj = stripe.Discount.construct_from({
      'source': {'coupon': {'id': 'coup_test', 'object': 'coupon'}},
    }, 'key')
    result = Discount.deserialize(stripe_obj)
    assert isinstance(result.get('coupon'), stripe.Coupon)

  def test_invoice_deserialize_with_taxes(self) -> None:
    """Invoice.deserialize loops over total_taxes entries → line 1452."""

    stripe_obj = stripe.Invoice.construct_from({
      'parent': None,
      'currency': 'usd',
      'total_discount_amounts': [],
      'total_taxes': [{'amount': 100}]
    }, 'key')
    result = Invoice.deserialize(stripe_obj)
    assert result.get('total_taxes_amount') == Decimal(1)

  def test_invoice_deserialize_str_subscription(self) -> None:
    """Invoice.deserialize with a string subscription ID → lines 1443-1444."""

    stripe_obj = stripe.Invoice.construct_from({
      'parent': {'subscription_details': {'subscription': 'sub_test'}},
      'currency': 'usd',
      'total_discount_amounts': [],
      'total_taxes': [],
    }, 'key')
    result = Invoice.deserialize(stripe_obj)
    assert result.get('subscription_id') == 'sub_test'

  def test_invoice_payment_deserialize_str_payment_intent(self) -> None:
    """InvoicePayment.deserialize with a string payment_intent ID."""

    stripe_obj = stripe.StripeObject.construct_from({
      'payment': {'type': 'payment_intent', 'payment_intent': 'pi_test'},
    }, 'key')
    result = InvoicePayment.deserialize(stripe_obj)
    assert result.get('payment_intent_id') == 'pi_test'
