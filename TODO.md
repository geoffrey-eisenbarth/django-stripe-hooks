- Finish testing suite
  - refund
  - charge
  - balance transactio
  - ConfirmationTokens
- Set up precommit
- README:
  - Describe how to set webhooks in Stripe, get API keys
  - document author hooks:
    - can optionally return response or None
    - stripe_obj / django_obj will be None if failed
- squash migrations, un-reinstall to tpg

old webhooks:

EVENT_NAMES = """
customer_created
promotion_code_updated
payment_method_attached
payment_method_automatically_updated
invoice_updated:
  invoice_finalized
  invoice_voided
  invoice_paid
customer_subscription_created (create SubscriptionItems too?)
customer_subscription_updated
charge_refunded
charge_succeeded

customer_deleted
customer_subscription_deleted (not deleted, just set to status=cancelled)

"""


  # TODO: related_objs
  # TODO: how to expand=['payment_method']?
  def invoice_updated(self) -> HttpResponse | None:
    # Update or create related objects locally
    if self.stripe_obj.payment_intent:
      stripe_pi = stripe.PaymentIntent.retrieve(
        self.stripe_obj.payment_intent,
        expand=['payment_method'],
      )
      PaymentIntent.from_stripe(stripe_pi)

      stripe_pm = stripe_pi.payment_method
      assert isinstance(stripe_pm, stripe.PaymentMethod)
      if stripe_pm.type == 'card':
        PaymentMethod.from_stripe(stripe_pm)
      elif stripe_pm.type == 'customer_balance':
        pass
      else:
        raise NotImplementedError(
          f"Unsupported PaymentMethod type: {stripe_pm.type}"
        )

    # Update or create Invoice locally
    Invoice.from_stripe(self.stripe_obj)

    return None

  # TODO: related_objs
  def charge_refunded(self) -> HttpResponse | None:
    for stripe_re in self.stripe_obj.refunds.data:
      stripe_txn = stripe.BalanceTransaction.retrieve(
        stripe_re.balance_transaction
      )
      BalanceTransaction.from_stripe(stripe_txn)
      Refund.from_stripe(stripe_re)

    return None

  # TODO: related_objs
  def charge_succeeded(self) -> HttpResponse | None:
    # Create the related BalanceTransaction first
    stripe_txn = stripe.BalanceTransaction.retrieve(
      self.stripe_obj.balance_transaction
    )
    BalanceTransaction.from_stripe(stripe_txn)

    # Now create the Charge
    Charge.from_stripe(self.stripe_obj)

    return None
