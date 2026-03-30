import pytest
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from django_stripe_hooks.apps import REQUIRED_SETTINGS


def test_stripe_config_ready_raises_error_on_missing_settings() -> None:
  app_config = apps.get_app_config('django_stripe_hooks')

  with patch('django_stripe_hooks.apps.settings') as mocked_settings:
    # spec=[] ensures hasattr() returns False for any attribute
    mocked_settings.mock_add_spec([])

    for setting in REQUIRED_SETTINGS:
      with pytest.raises(ImproperlyConfigured):
        app_config.ready()
