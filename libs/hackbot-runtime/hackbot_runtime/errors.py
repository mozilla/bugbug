class AgentError(Exception):
    """Raise from an agent's ``main()`` to fail the run with a clear message.

    The runtime records the message as the run's ``error`` in ``summary.json``
    and exits non-zero. Any other exception fails the run too — ``AgentError``
    just reads as a deliberate, expected failure rather than a crash.
    """
