from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import models
from django.forms import ModelForm
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from django_hosts.resolvers import reverse
from import_export import resources
from import_export.admin import ExportActionMixin
from import_export.fields import Field
from import_export.formats import base_formats
from rangefilter.filters import DateRangeFilter

from django_stripe_hooks.models import (
  Product, Price, PriceTier,
  Coupon, PromotionCode,
  Customer, PaymentMethod, Subscription,
  PaymentIntent, BalanceTransaction, Invoice, Charge, Refund
)


class ActiveModelAdmin(admin.ModelAdmin):
  """Replaces model deletion with deactivation."""

  actions = ['activate_selected', 'deactivate_selected']

  # Quick and dirty way to hide `active` field
  formfield_overrides = {
    models.BooleanField: {'widget': forms.HiddenInput},
  }

  class Media:
    css = {
      'all': ('css/admin.css', ),
    }
    js = ('js/admin/active-model-form.js', )

  def activate_selected(self, request, queryset):
    """Activates instances in the selected QuerySet.

    Notes
    -----
    Since `QuerySet.update()` is converted directly to a SQL statement,
    it bypasses our custom `save()` logic that updates third party API
    references. As a result, we forego the SQL efficiency for a loop
    over the relevant objects to make sure our code gets run.

    """
    for obj in queryset:
      obj.active = True
      obj.save()
  activate_selected.short_description = 'Activate selected %(verbose_name_plural)s'

  def deactivate_selected(self, request, queryset):
    """Deactivates instances in the selected QuerySet.

    Notes
    -----
    Sine `QuerySet.update()` is converted directly to a SQL statement,
    it bypasses our custom `save()` logic that updates third party API
    references. As a result, we exchange the SQL efficiency for a loop
    over the relevant objects.

    """
    for obj in queryset:
      obj.active = False
      obj.save(update_fields=['active'])
  deactivate_selected.short_description = 'Deactivate selected %(verbose_name_plural)s'

  def get_actions(self, request):
    actions = super().get_actions(request)
    if 'delete_selected' in actions:
      del actions['delete_selected']
    return actions

  def get_readonly_fields(self, request, obj=None):
    readonly_fields = super().get_readonly_fields(request, obj=obj)
    readonly_fields = [f for f in readonly_fields if f != 'active']
    return readonly_fields

  def change_view(self, request, object_id, form_url='', extra_context=None):
    """Override ChangeView to set Activate/Deactivate buttons."""

    object_id = object_id.replace('_5F', '_')  # Django bugs #22226 and #28460

    extra_context = extra_context or {}
    extra_context.update({
      'is_active_model': True,
      'is_active': self.model.objects.get(pk=object_id).active,
    })

    change_view = super().change_view(
      request,
      object_id,
      form_url=form_url,
      extra_context=extra_context,
    )
    return change_view


class ProtectedModelAdmin(admin.ModelAdmin):
  """Prevent deletion in the Django admin."""
  def has_delete_permission(self, request, obj=None):
    return False


class RestrictedModelAdmin(admin.ModelAdmin):
  """Prevent creation in the Django admin."""
  def has_add_permission(self, request, obj=None):
    return False


class ReadonlyModelAdmin(admin.ModelAdmin):
  """Prevent edits in the Django admin."""

  #list_display_links = None

  def has_change_permission(self, request, obj=None):
    return False

  def get_readonly_fields(self, request, obj=None):
    if obj:
      readonly_fields = [
        field.name
        for field in obj._meta.fields
        if field.name != 'active'
      ]
    else:
      readonly_fields = []
    return readonly_fields


class ViewModelAdmin(
  ProtectedModelAdmin,
  RestrictedModelAdmin,
  ReadonlyModelAdmin
):
  pass
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
  """Django admin for Stripe Products."""

  list_display = (
    'name',
  )

  fieldsets = (
    (None, {
      'fields': (
        'name',
        'description',
      ),
    }),
  )

  class Media:
    css = {
      'all': (
        'css/admin.css',
      ),
    }


class PriceTierInline(admin.TabularInline):
  model = PriceTier
  extra = 2
  fields = ('flat_amount', 'unit_amount', 'up_to')

  def has_change_permission(self, request, obj):
    return False


# TODO: This uses custom admin html and css. Is it needed?
@admin.register(Price)
class PriceAdmin(ReadonlyModelAdmin, ActiveModelAdmin):
  """Django admin for Stripe Prices."""

  list_display = (
    'nickname',
    'product',
    'type',
    'interval',
    'unit_amount',
    'active',
  )
  list_select_related = ('product', )

  fieldsets = (
    (None, {
      'fields': (
        'product',
        'nickname',
        'active',
      ),
    }),
    (_('Billing Details'), {
      'fields': (
        'unit_amount',
        'type',
        'interval',
        'usage_type',
        'billing_scheme',
        'tiers_mode',
      ),
    }),
  )

  class Media:
    extend = True
    js = (
      'js/admin/toggle-inlines.js',
    )

  def get_inlines(self, request, obj=None):
    """Prevent editing PriceTier inlines once an object is saved.

    Notes
    -----
    The OrderedInlineModelAdminMixin requires that we list the OrderedInline
    above in the `inline` attribute, so we can't delete it in favor of this
    method.

    """

    if (not obj) or (obj.tiers.count() > 0):
      inlines = (PriceTierInline, )
    else:
      inlines = tuple()
    return inlines


@admin.register(Coupon)
class CouponAdmin(ReadonlyModelAdmin):
  """Django admin for Stripe Coupons."""

  list_display = (
    'name',
    'terms',
  )

  fieldsets = (
    (None, {
      'fields': (
        'name',
        'duration',
        'percent_off',
        'amount_off',
        'products',
      ),
    }),
  )
  filter_horizontal = ('products', )


@admin.register(PromotionCode)
class PromotionCodeAdmin(ReadonlyModelAdmin):
  """Django admin for Stripe Coupons."""

  list_display = (
    'code',
    'coupon',
    'terms',
    'expires_at',
    'redemptions',
    'active',
    'link',
  )
  list_select_related = ('coupon', )
  list_display_links = None

  fieldsets = (
    (None, {
      'fields': (
        'code',
        'coupon',
        'expires_at',
        'max_redemptions',
        'active',
      ),
    }),
  )

  def link(self, obj) -> str:
    if product := obj.coupon.products.first():
      html = format_html(
        '<a href="{url}?{params}">{link_symbol}</a>',
        url=reverse('shopping_cart', host='www'),
        params=f'price={product.prices.first().api_id}&promo={obj.api_id}',
        link_symbol=mark_safe('&#128279'),
      )
    else:
      html = 'N/A'
    return html


class SubscriptionInline(admin.TabularInline):
  """Django inline admin for Customer Subscriptions."""

  model = Subscription
  extra = 0
  fields = (
    'product_category',
    'status',
    'current_period_start',
    'current_period_end',
    'promotion_code',
    'cancel_at_period_end',
  )

  def product_category(self, obj: Subscription) -> str:
    metadata = obj.items.first().metadata
    return metadata.get('category', '').title()
  product_category.short_description = 'Category'

  def has_add_permission(self, request, obj=None):
    return False

  def has_delete_permission(self, request, obj=None):
    return False

  def get_readonly_fields(self, request, obj=None):
    if obj:
      readonly_fields = (
        'status',
        'current_period_start',
        'current_period_end',
      )
    else:
      readonly_fields = super().get_readonly_fields(request, obj=obj)
    return readonly_fields


class PaymentMethodInline(admin.TabularInline):
  """Django inline admin for Customer PaymentMethods."""

  model = PaymentMethod
  extra = 0
  fields = (
    'card_info',
    'card_exp_date',
    'zip_code',
    'is_default',
  )
  readonly_fields = ('card_info', )

  def has_add_permission(self, request, obj=None):
    return False

  def has_delete_permission(self, request, obj=None):
    return False


class CreatePaymentIntentInline(admin.TabularInline):
  """Django inline admin for creating Customer PaymentIntents."""

  model = PaymentIntent
  verbose_name = _("CREATE ONE-TIME CHARGE")
  verbose_name_plural = _("CREATE ONE-TIME CHARGE")
  min_num = max_num = extra = 1

  fields = (
    'amount',
    'currency',
    'description',
    'payment_method',
  )

  def has_change_permission(self, request, obj=None):
    return False

  def has_view_permission(self, request, obj=None):
    return False

  def has_delete_permission(self, request, obj=None):
    return False

  def get_formset(self, request, obj=None, **kwargs):
    """Set parent Customer object as attribute."""
    self.customer = obj
    return super().get_formset(request, obj, **kwargs)

  def formfield_for_foreignkey(self, db_field, request, **kwargs):
    if db_field.name == 'payment_method':
      # Note: `self.customer` is set during `get_formset()` method
      kwargs['queryset'] = PaymentMethod.objects.filter(customer=self.customer)
    return super().formfield_for_foreignkey(db_field, request, **kwargs)


class InvoiceInline(admin.TabularInline):
  """Django inline admin for Customer Invoices."""

  model = Invoice
  extra = 0
  fields = (
    'total',
    'status',
    'period_start',
    'period_end',
    'pdf_link',
  )

  def pdf_link(self, obj):
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice_pdf,
    )
    return html
  pdf_link.short_description = 'PDF'

  def has_add_permission(self, request, obj=None):
    return False

  def has_delete_permission(self, request, obj=None):
    return False

  def get_readonly_fields(self, request, obj=None):
    if obj:
      readonly_fields = (
        'pdf_link',
        'total',
        'status',
        'subscription',
        'period_start',
        'period_end',
      )
    else:
      readonly_fields = super().get_readonly_fields(request, obj=obj)
    return readonly_fields


@admin.register(Customer)
class CustomerAdmin(RestrictedModelAdmin):
  """Django admin for Stripe Customers."""

  search_fields = (
    'email',
    'name',
    'phone',
  )

  list_display = (
    'email',
    'name',
    'phone',
    'synced',
  )

  fieldsets = (
    (None, {'fields': ()}),
  )

  inline_type = 'stacked'
  inlines = (
    SubscriptionInline,
    PaymentMethodInline,
    InvoiceInline,
    # CreatePaymentIntentInline,
  )

  def synced(self, obj):
    return bool(obj.api_id)
  synced.boolean = True


class SubscriptionForm(ModelForm):
  """Add custom validation for Subscription and Coupons."""

  class Meta:
    model = Subscription
    fields = '__all__'

  def clean(self):
    coupon = self.cleaned_data['promotion_code'].coupon
    price = self.cleaned_data['prices']
    if coupon and (coupon.product.api_id != price.product.api_id):
      message = (
        "The selected coupon is not applicable to this subscription's "
        "product."
      )
      raise ValidationError(message)
    return self.cleaned_data


@admin.register(Subscription)
class SubscriptionAdmin(ViewModelAdmin):
  """Django admin for Stripe Subscriptions.

  Notes
  -----
  Subscriptions are not StripeActiveModels, even though
  they have similar functionality. This is because the
  status of a Subscription is not determined by an `active`
  attribute, but rather by the value of the `status` field.

  """

  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )
  list_display = (
    'customer',
    'status_verbose',
    'product_category',
    'promotion_code',
    'current_period_end',
    'synced',
  )
  list_select_related = (
    'customer',
  )

  form = SubscriptionForm
  fieldsets = (
    (None, {
      'fields': (
        'customer',
        'current_period_start',
        'current_period_end',
        'promotion_code',
        'cancel_at_period_end',
      ),
    }),
  )
  inlines = (InvoiceInline, )

  def status_verbose(self, obj):
    """Custom attribute for ListDisplay."""
    if obj.cancel_at_period_end:
      status = 'cancels'
    else:
      status = obj.status
    text = dict(Subscription.STATUSES)
    text.update({
      'active': f'Renews {obj.current_period_end:%b %d, %Y}',
      'cancels': f'Cancels {obj.current_period_end:%b %d, %Y}',
    })
    return text[status]
  status_verbose.short_description = 'Status'
  status_verbose.admin_order_field = 'status'

  def product_category(self, obj: Subscription) -> str:
    metadata = obj.items.first().metadata
    return metadata.get('category', '').title()
  product_category.short_description = 'Category'

  def synced(self, obj):
    return bool(obj.api_id)

  def get_readonly_fields(self, request, obj=None):
    if obj:
      readonly_fields = (
        'customer',
        'current_period_start',
        'current_period_end',
      )
    else:
      readonly_fields = super().get_readonly_fields(request, obj=None)
    return readonly_fields


@admin.register(PaymentIntent)
class PaymentIntentAdmin(ViewModelAdmin):
  """Django admin for Stripe PaymentIntents."""

  search_fields = (
    'amount',
    'description',
    'customer__email',
    'customer__name',
    'customer__phone',
  )
  list_display = (
    'payment_method',
    'amount',
    'customer',
    'description',
    'status',
  )


class BalanceTransactionResource(resources.ModelResource):
  """Import-Export Resource for BalanceTransactions."""

  customer_name = Field()
  customer_email = Field()
  invoice_url = Field()
  products = Field()

  class Meta:
    model = BalanceTransaction
    fields = (
      'api_id',
      'customer_name',
      'customer_email',
      'products',
      'type',
      'currency',
      'amount',
      'fee',
      'net',
      'status',
      'available_on',
      'invoice_url',
    )
    export_order = fields

  def dehydrate_customer_name(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      name = charge.customer.name
    elif refund := bt.refunds.first():
      name = refund.charge.customer.name
    else:
      name = 'N/A'
    return name

  def dehydrate_customer_email(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      email = charge.customer.email
    elif refund := bt.refunds.first():
      email = refund.charge.customer.email
    else:
      email = 'N/A'
    return email

  def dehydrate_invoice_url(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      url = charge.invoice.invoice_pdf
    else:
      url = 'N/A'
    return url

  def dehydrate_products(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      items = charge.invoice.subscription.items.all()
      products = ', '.join(items.values_list('price__nickname', flat=True))
    else:
      products = 'N/A'
    return products


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(ExportActionMixin, ViewModelAdmin):
  """Django admin for Stripe BalanceTransactions."""

  resource_classes = (
    BalanceTransactionResource,
  )
  formats = (
    base_formats.CSV,
    base_formats.XLS,
    base_formats.XLSX,
    base_formats.JSON,
  )

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
  list_filter = (
    ('available_on', DateRangeFilter),
  )
  list_display_links = None

  def _currency_display(self, obj, field):
    """Displays currency-related fields."""
    amount = getattr(obj, field)
    if field == 'fee' or amount < 0:
      sign = '-'
    elif amount > 0:
      sign = '+'
    elif amount == 0:
      sign = ''

    if obj.currency == 'usd':
      s = f"{sign}${abs(amount)} USD"
    else:
      s = f"{sign} {abs(amount) * 100} {obj.currency.upper()}"
    return s

  def amount_display(self, obj):
    return self._currency_display(obj, 'amount')
  amount_display.short_description = _("Amount")
  amount_display.admin_order_field = 'amount'

  def fee_display(self, obj):
    return self._currency_display(obj, 'fee')
  fee_display.short_description = _("Fee")
  fee_display.admin_order_field = 'fee'

  def net_display(self, obj):
    return self._currency_display(obj, 'net')
  net_display.short_description = _("Net")
  net_display.admin_order_field = 'net'


@admin.register(Invoice)
class InvoiceAdmin(ViewModelAdmin):
  """Django admin for Stripe Invoices."""

  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'customer',
    'total',
    'period_start',
    'period_end',
    'status_chip',
    'product_category',
    'pdf_link',
  )
  list_select_related = (
    'customer',
    'subscription',
  )
  list_filter = (
    'period_start',
  )
  list_display_links = None

  def status_chip(self, obj: Invoice) -> str:
    """Format to add badge HTML."""
    STATUSES = {
      'draft': ('Draft', 'info'),
      'open': ('Open', 'warn'),
      'paid': ('Paid', 'ok'),
      'uncollectible': ('Uncollectible', 'bad'),
      'void': ('Void', 'bad'),
    }
    status, color = STATUSES[obj.status]
    html = f'<chip class="{color}">{status}</chip>'
    return mark_safe(html)
  status_chip.short_description = 'Status'
  status_chip.admin_order_field = 'status'

  def product_category(self, obj: Invoice) -> str:
    metadata = obj.subscription.items.first().metadata
    return metadata.get('category', '').title()
  product_category.short_description = 'Category'

  def pdf_link(self, obj):
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice_pdf,
    )
    return html
  pdf_link.short_description = 'PDF'


@admin.register(Charge)
class ChargeAdmin(ViewModelAdmin):
  """Django admin for Stripe Charges."""

  search_fields = (
    'customer__email',
    'customer__name',
    'customer__phone',
  )

  list_display = (
    'customer',
    'amount',
    'status',
    'created',
    'description',
    'disputed',
    'refunded',
    'invoice_link',
  )
  list_select_related = (
    'customer',
    'invoice',
  )

  def invoice_link(self, obj):
    html = format_html(
      '<a href="{url}">PDF</a>',
      url=obj.invoice.invoice_pdf,
    )
    return html

  invoice_link.short_description = 'Invoice'


@admin.register(Refund)
class RefundAdmin(ProtectedModelAdmin):
  """Django admin for Stripe Refunds."""

  list_display = (
    'charge__customer',
    'amount',
    'reason',
    'status',
  )

  fieldsets = (
    (None, {
      'fields': (
        'charge',
        'reason',
        ('amount', 'currency'),
      ),
    }),
  )

  def charge__customer(self, obj):
    return obj.charge.customer
  charge__customer.short_description = _("Customer")
