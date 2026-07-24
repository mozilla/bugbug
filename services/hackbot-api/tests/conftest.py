import os

# The global Settings embeds required nested models, validated when Settings() is
# built at import: PhabricatorSettings needs a 32-char api_key, and
# WebhookSettings needs a secret. Provide dummies here (before app.config is
# imported) so the suite imports even in tests that don't exercise these.
# `setdefault` leaves any real env value intact.
os.environ.setdefault("PHABRICATOR_API_KEY", "api-" + "a" * 28)
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
