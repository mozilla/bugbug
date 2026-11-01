"""Deploy tooling for hackbot agents.

Three pieces, one per module:

- :mod:`hackbot_deploy.schema`: the ``[deploy]`` table of an agent's
  ``hackbot.toml``.
- :mod:`hackbot_deploy.manifest`: turns every agent's ``[deploy]`` table into
  ``agents.json``, the file the Terraform in mozilla/webservices-infra reads.
- :mod:`hackbot_deploy.deploy`: ships an image onto an agent's infrastructure,
  gated on that infrastructure having the shape the image was built for.
"""
