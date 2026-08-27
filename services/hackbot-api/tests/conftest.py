import os

# The global Settings embeds required nested models, validated when Settings() is
# built at import: PhabricatorSettings needs a 32-char api_key, WebhookSettings
# needs a secret, BugzillaWebhookSettings needs its own, and SlackSettings needs a
# signing secret. Provide dummies here (before app.config is imported) so the suite
# imports even in tests that don't exercise these. `setdefault` leaves any real env
# value intact.
os.environ.setdefault("PHABRICATOR_API_KEY", "api-" + "a" * 28)
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("BUGZILLA_WEBHOOK_SECRET", "test-bugzilla-webhook-secret")
os.environ.setdefault("BUGZILLA_WEBHOOK_BOT_LOGIN", "hackbot@mozilla.tld")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")
