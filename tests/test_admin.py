from django.contrib import admin
from django.db import models
from django.http import HttpRequest
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model

from django_stripe_hooks.models import StripeModel


User = get_user_model()


class StripeAdminTest(TestCase):
  def setUp(self) -> None:
    self.admin_user = User.objects.create_superuser(
      username='admin',
      email='admin@test.com',
      password='password'
    )
    self.client.force_login(self.admin_user)

  def test_changelist_views(self) -> None:
    """Iterate through all StripeModel admins to check custom properties."""

    for model in StripeModel.__subclasses__():
      if model not in admin.site._registry:
        continue

      name = f'{model._meta.app_label}_{model._meta.model_name}'
      url = reverse(f'admin:{name}_changelist')

      response = self.client.get(url)
      assert response.status_code == 200

  def test_admin_permissions(self) -> None:
    """Verify that all StripeModel admins are read-only."""

    factory = RequestFactory()
    request = factory.get('/')
    request.user = self.admin_user

    for model in StripeModel.__subclasses__():
      if model not in admin.site._registry:
        continue

      model_admin = admin.site._registry[model]
      self.assert_readonly(model_admin, request)

      for inline_class in getattr(model_admin, 'inlines', []):
        inline_instance = inline_class(model, admin.site)
        self.assert_readonly(inline_instance, request)

  def assert_readonly(
    self,
    admin_obj: admin.options.BaseModelAdmin[models.Model],
    request: HttpRequest,
  ) -> None:
    # Assertions for the three primary write permissions
    self.assertFalse(admin_obj.has_add_permission(request))
    self.assertFalse(admin_obj.has_change_permission(request))
    self.assertFalse(admin_obj.has_delete_permission(request))
