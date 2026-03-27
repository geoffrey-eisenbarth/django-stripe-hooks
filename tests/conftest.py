import os
import re
import subprocess
import time

import pytest

from django.conf import settings


WEBHOOK_URL = 'localhost:8888/stripe/webhooks/'


@pytest.fixture(scope='session', autouse=True)
def check_stripe_installed():
  try:
    subprocess.run(['stripe', '--version'], capture_output=True, check=True)
  except (subprocess.CalledProcessError, FileNotFoundError):
    pytest.exit('Stripe CLI is not installed.')


@pytest.fixture(scope='session', autouse=True)
def stripe_cli_setup():
  """
  Starts the Stripe CLI, captures the dynamic signing secret,
  and updates Django settings for the duration of the test session.
  """

  log_file = open('stripe_cli.log', 'w')

  # Get Stripe keys from env and inject to process environment
  stripe_key = getattr(settings, "STRIPE_SECRET_KEY", None)
  env = os.environ.copy()
  if stripe_key:
    env["STRIPE_API_KEY"] = stripe_key
  else:
    pytest.exit(
      "STRIPE_SECRET_KEY not found in settings. Check your .env file."
    )

  # Start the Stripe CLI listener in the background
  process = subprocess.Popen(
    ['stripe', 'listen', '--forward-to', WEBHOOK_URL],
    stdout=log_file,
    stderr=subprocess.STDOUT,
    text=True,
    env=env,
  )

  # Capture the Webhook Secret from the CLI output
  wh_secret = None
  start_time = time.time()

  # Wait up to 10 seconds for the secret to appear in the logs
  while time.time() - start_time < 10:
    log_file.flush()  # Force write to disk so we can read it
    with open('stripe_cli.log', 'r') as f:
      content = f.read()
      if 'whsec_' in content:
        match = re.search(r'whsec_[a-zA-Z0-9]+', content)
        if match:
          wh_secret = match.group(0)
          break
      time.sleep(0.5)

  if not wh_secret:
    process.terminate()
    log_file.close()
    pytest.exit(
      'Failed to capture secret. '
      f'CLI Exit Code: {process.poll()}. '
      'Check stripe_cli.log'
    )

  # Inject the secret into Django settings
  settings.STRIPE_WEBHOOK_SECRET_KEY = wh_secret

  try:
    # The tests run here
    yield
  finally:
    # Shutdown the CLI after tests are done
    process.terminate()
    process.wait()
    log_file.close()
