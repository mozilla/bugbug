from fastapi import Request
from hackbot_client import HackbotClient


def get_hackbot_client(request: Request) -> HackbotClient:
    """Dependency: the app-scoped client for the public hackbot API."""
    return request.state.hackbot_client
