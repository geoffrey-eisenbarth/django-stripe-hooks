from decimal import Decimal
import time
from typing import Type, TypeVar, Any, Generator

import stripe
import pytest

from django.db import models
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.core.signals import got_request_exception
from django.http import HttpRequest

from django_stripe_hooks import models as stripe_models
from pytest_django.live_server_helper import LiveServer


T = TypeVar('T', bound=models.Model)


@pytest.mark.django_db
class TestStripeWebhooks:

  @pytest.fixture(autouse=True)
  def setup_stripe(self) -> None:
    stripe.api_key = settings.STRIPE_SECRET_KEY

  @pytest.fixture(autouse=True)
  def fail_on_server_exception(self) -> Generator[None, None, None]:
    exceptions = []

    def signal_handler(
      sender: WSGIHandler,
      request: HttpRequest,
      **kwargs: Any,
    ) -> None:
      # Capture the actual exception object for the error message
      exceptions.append(kwargs.get('exception'))

    # Connect the signal before the test starts
    got_request_exception.connect(signal_handler)

    yield

    # Disconnect after the test to prevent side effects in other tests
    got_request_exception.disconnect(signal_handler)

    if exceptions:
      # Fail the test with the details of the first exception found
      pytest.fail(
        f'Server-side exception during webhook processing: {exceptions[0]}'
      )

  def wait_for_object(
    self,
    model_class: Type[T],
    timeout: int = 10,
    **kwargs: Any,
  ) -> T:
    """Generic polling logic to detect object existence."""
    retries = timeout * 2
    while retries > 0:
      if obj := model_class.objects.filter(**kwargs).first():
        return obj
      time.sleep(0.5)
      retries -= 1
    pytest.fail(
      f'Timed out waiting for {model_class.__name__} with {kwargs=}'
    )

  def test_products_and_billing(self, live_server: LiveServer) -> None:
    """Integration testing for Product and Billing primatives."""

    # Product primatives
    s_product = stripe.Product.create(
      name=f'Test Product {time.time()}',
      description='Description for test product',
      statement_descriptor='STATEMENT',
      metadata={'category': 'Test Product'},
    )
    s_price = stripe.Price.create(
      product=s_product.id,
      nickname='Test Nickname',
      unit_amount=2000,
      currency='usd',
      recurring={'interval': 'year'},
      metadata={'category': 'Test Price'},
    )
    s_coupon = stripe.Coupon.create(
      name='Test Coupon',
      percent_off=20,
      duration='once',
      applies_to={'products': [s_product.id]},
    )
    s_promo = stripe.PromotionCode.create(
      coupon=s_coupon.id,
      code=f'PROMO_{int(time.time())}'
    )

    d_product = self.wait_for_object(stripe_models.Product, id=s_product.id)
    d_price = self.wait_for_object(stripe_models.Price, id=s_price.id)
    d_coupon = self.wait_for_object(stripe_models.Coupon, id=s_coupon.id)
    d_promo = self.wait_for_object(stripe_models.PromotionCode, id=s_promo.id)

    # Billing primatives
    s_customer = stripe.Customer.create(
      name='Test Customer',
      email='test@customer.com',
    )
    s_payment_method = stripe.PaymentMethod.create(
      type='card',
      card={'token': 'tok_visa'},
    )
    stripe.PaymentMethod.attach(
      s_payment_method.id,
      customer=s_customer.id,
    )
    s_subscription = stripe.Subscription.create(
      customer=s_customer.id,
      items=[{'price': s_price.id, 'quantity': 1}],
      discounts=[{'promotion_code': s_promo.id}],
      default_payment_method=s_payment_method.id,
      collection_method='charge_automatically',
      payment_behavior='default_incomplete',
    )
    s_fi = stripe.Customer.create_funding_instructions(
      s_customer.id,
      funding_type='bank_transfer',
      bank_transfer={'type': 'us_bank_transfer'},
      currency='usd',
    )

    d_customer = self.wait_for_object(
      stripe_models.Customer,
      id=s_customer.id
    )
    d_payment_method = self.wait_for_object(
      stripe_models.PaymentMethod,
      id=s_payment_method.id
    )
    d_subscription = self.wait_for_object(
      stripe_models.Subscription,
      id=s_subscription.id,
    )
    d_invoice = self.wait_for_object(
      stripe_models.Invoice,
      timeout=20,
      customer_id=s_customer.id,
      subscription_id=s_subscription.id,
    )

    # Test related objects
    assert d_price.product.id == d_product.id
    assert d_promo.coupon.id == d_coupon.id
    assert d_payment_method.customer.id == d_customer.id

    # Test currency unit conversion
    assert d_price.unit_amount == Decimal(20.00)

    # Confirm Invoice was updated as paid
    d_invoice.refresh_from_db()
    # TODO: assert d_invoice.status == 'paid'

    # Confirm Subscription is active
    d_subscription.refresh_from_db()
    # TODO: assert d_subscription.status == 'active'

    # Confirm FundingInstructions were created
    d_fi = stripe_models.FundingInstructions.from_stripe(d_customer, s_fi)
    assert d_fi.customer.id == s_customer.id
