from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe

from django_stripe_hooks.utils import fetch, StripeService


def make_service(model_name: str) -> MagicMock:
  """Return a mock whose class name matches Stripe's naming convention."""
  service = MagicMock()
  service.__class__.__name__ = f'{model_name}Service'
  return service


class TestStripeServiceProtocol:
  def test_concrete_class_satisfies_protocol(self) -> None:
    class MyService:
      def retrieve(
        self,
        id: str,
        params: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
      ) -> stripe.StripeObject:
        raise NotImplementedError

    assert isinstance(MyService(), StripeService)


class TestFetch:
  def test_success(self) -> None:
    service = make_service('Customer')
    expected = MagicMock(spec=stripe.Customer)
    service.retrieve.return_value = expected

    result = fetch(service, 'cus_123')

    assert result is expected
    service.retrieve.assert_called_once()

  def test_rate_limit_then_success(self) -> None:
    service = make_service('Customer')
    expected = MagicMock(spec=stripe.Customer)
    service.retrieve.side_effect = [
      stripe.error.RateLimitError(),
      stripe.error.RateLimitError(),
      expected,
    ]

    with patch('time.sleep') as mock_sleep:
      result = fetch(service, 'cus_123')

    assert result is expected
    assert mock_sleep.call_count == 2
    # Back-off increases: 0.5*1, 0.5*2
    mock_sleep.assert_any_call(0.5)
    mock_sleep.assert_any_call(1.0)

  def test_rate_limit_exhausted(self) -> None:
    service = make_service('Customer')
    service.retrieve.side_effect = stripe.error.RateLimitError()

    with patch('time.sleep'):
      with pytest.raises(stripe.error.RateLimitError):
        fetch(service, 'cus_123')

    assert service.retrieve.call_count == 5

  def test_404_returns_none(self) -> None:
    service = make_service('Customer')
    service.retrieve.side_effect = stripe.error.InvalidRequestError(  # type: ignore[no-untyped-call]  # noqa: E501
      message='No such customer',
      param='id',
      http_status=404,
    )

    result = fetch(service, 'cus_missing')

    assert result is None

  def test_resource_missing_returns_none(self) -> None:
    service = make_service('Customer')
    service.retrieve.side_effect = stripe.error.InvalidRequestError(  # type: ignore[no-untyped-call]  # noqa: E501
      message='No such customer',
      param='id',
      code='resource_missing',
    )

    result = fetch(service, 'cus_missing')

    assert result is None

  def test_other_invalid_request_reraises(self) -> None:
    service = make_service('Customer')
    exc = stripe.error.InvalidRequestError(  # type: ignore[no-untyped-call]
      message='Invalid parameter',
      param='email',
      http_status=400,
    )
    service.retrieve.side_effect = exc

    with pytest.raises(stripe.error.InvalidRequestError) as exc_info:
      fetch(service, 'cus_123')

    assert exc_info.value is exc
