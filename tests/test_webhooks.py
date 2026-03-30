from decimal import Decimal
import time
from typing import TypeVar, Any, Generator, cast

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


@pytest.mark.django_db(transactional=True)
class TestStripeWebhooks:

  @pytest.fixture(autouse=True)
  def setup_stripe(self) -> None:
    self.stripe_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

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
    model_class: type[T],
    timeout: int = 10,
    **kwargs: Any,
  ) -> T:
    """Generic polling logic to detect object existence."""
    assert stripe_models.has_manager(model_class)
    retries = timeout * 2
    while retries > 0:
      if obj := model_class.objects.filter(**kwargs).first():
        return cast(T, obj)
      time.sleep(0.5)
      retries -= 1
    pytest.fail(
      f'Timed out waiting for create on {model_class.__name__} with {kwargs=}'
    )

  def wait_for_delete(
    self,
    model_class: type[T],
    timeout: int = 10,
    **kwargs: Any,
  ) -> None:
    """Generic polling logic to detect object existence."""
    assert stripe_models.has_manager(model_class)
    retries = timeout * 2
    while retries > 0:
      if not model_class.objects.filter(**kwargs).exists():
        return
      time.sleep(0.5)
      retries -= 1
    pytest.fail(
      f'Timed out waiting for delete on {model_class.__name__} with {kwargs=}'
    )

  def test_create(self, live_server: LiveServer) -> None:
    """Integration testing for Product and Billing primatives."""

    # Product primatives
    s_product = self.stripe_client.v1.products.create(params={
      'name': f'Test Product {time.time()}',
      'description': 'Description for test product',
      'statement_descriptor': 'STATEMENT',
      'metadata': {'category': 'Test Product'},
    })
    s_price = self.stripe_client.v1.prices.create(params={
      'product': s_product.id,
      'nickname': 'Test Nickname',
      'unit_amount': 2000,
      'currency': 'usd',
      'recurring': {'interval': 'year'},
      'metadata': {'category': 'Test Price'},
    })
    s_coupon = self.stripe_client.v1.coupons.create(params={
      'name': f'Test Coupon {time.time()}',
      'percent_off': 20,
      'duration': 'once',
      'applies_to': {'products': [s_product.id]},
    })
    s_promo = self.stripe_client.v1.promotion_codes.create(params={
      'promotion': {
        'type': 'coupon',
        'coupon': s_coupon.id,
      },
      'code': f'PROMO_{int(time.time())}'
    })

    d_product = self.wait_for_object(stripe_models.Product, id=s_product.id)
    d_price = self.wait_for_object(stripe_models.Price, id=s_price.id)
    d_coupon = self.wait_for_object(stripe_models.Coupon, id=s_coupon.id)
    d_promo = self.wait_for_object(stripe_models.PromotionCode, id=s_promo.id)

    # Billing primatives
    s_customer = self.stripe_client.v1.customers.create(params={
      'name': 'Test Customer',
      'email': 'test@customer.com',
    })
    s_payment_method = self.stripe_client.v1.payment_methods.create(params={
      'type': 'card',
      'card': {'token': 'tok_visa'},
    })
    self.stripe_client.v1.payment_methods.attach(
      s_payment_method.id,
      params={'customer': s_customer.id},
    )
    s_subscription = self.stripe_client.v1.subscriptions.create(params={
      'customer': s_customer.id,
      'items': [{'price': s_price.id, 'quantity': 1}],
      'discounts': [{'promotion_code': s_promo.id}],
      'default_payment_method': s_payment_method.id,
      'collection_method': 'charge_automatically',
      'payment_behavior': 'default_incomplete',
    })

    d_customer = self.wait_for_object(
      stripe_models.Customer,
      id=s_customer.id
    )
    d_payment_method = self.wait_for_object(
      stripe_models.PaymentMethod,
      id=s_payment_method.id
    )
    d_payment_intent = self.wait_for_object(
      stripe_models.PaymentIntent,
      customer_id=s_customer.id,
      payment_method_id=s_payment_method.id,
    )

    # Confirm PaymentIntent, verify Invoice and Subscription updated
    s_payment_intent = self.stripe_client.v1.payment_intents.confirm(
      d_payment_intent.id,
    )
    self.wait_for_object(
      stripe_models.Invoice,
      timeout=20,
      customer_id=s_customer.id,
      subscription_id=s_subscription.id,
      status='paid',
    )
    d_subscription = self.wait_for_object(
      stripe_models.Subscription,
      id=s_subscription.id,
      status='active',
    )

    # Refund the charge
    s_refund = self.stripe_client.v1.refunds.create(params={
      'amount': s_payment_intent.amount,
      'payment_intent': s_payment_intent.id,
      'reason': 'requested_by_customer',
    })
    self.wait_for_object(
      stripe_models.Refund,
      id=s_refund.id,
    )

    # Test ForeignKey relations
    assert d_price.product.id == d_product.id
    assert d_promo.coupon.id == d_coupon.id
    assert d_payment_method.customer.id == d_customer.id

    # Test ManyToMany relations
    assert d_coupon.products.exists()

    # Test ReverseForeignKey relations
    d_subscription.refresh_from_db()
    assert d_subscription.items.exists()

    # Test currency unit conversion
    assert d_price.unit_amount == Decimal(20.00)

  def test_delete(self, live_server: LiveServer) -> None:
    s_product = self.stripe_client.v1.products.create(params={
      'name': f'Delete Product {time.time()}',
      'description': 'Description for delete product',
      'statement_descriptor': 'STATEMENT',
      'metadata': {'category': 'Delete Product'},
    })
    s_coupon = self.stripe_client.v1.coupons.create(params={
      'name': f'Test Coupon {time.time()}',
      'percent_off': 20,
      'duration': 'once',
      'applies_to': {'products': [s_product.id]},
    })
    s_customer = self.stripe_client.v1.customers.create(params={
      'name': 'Delete Customer',
      'email': 'delete@customer.com',
    })

    d_product = self.wait_for_object(stripe_models.Product, id=s_product.id)
    d_coupon = self.wait_for_object(stripe_models.Coupon, id=s_coupon.id)
    d_customer = self.wait_for_object(stripe_models.Customer, id=s_customer.id)

    self.stripe_client.v1.products.delete(s_product.id)
    self.stripe_client.v1.coupons.delete(s_coupon.id)
    self.stripe_client.v1.customers.delete(s_customer.id)

    self.wait_for_delete(stripe_models.Product, id=s_product.id)
    self.wait_for_delete(stripe_models.Coupon, id=s_coupon.id)
    self.wait_for_delete(stripe_models.Customer, id=s_customer.id)

    with pytest.raises(stripe_models.Product.DoesNotExist):
      d_product.refresh_from_db()
    with pytest.raises(stripe_models.Coupon.DoesNotExist):
      d_coupon.refresh_from_db()
    with pytest.raises(stripe_models.Customer.DoesNotExist):
      d_customer.refresh_from_db()
