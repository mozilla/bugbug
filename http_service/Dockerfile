ARG BUGBUG_VERSION=latest

FROM mozilla/bugbug-base:$BUGBUG_VERSION

# Install dependencies first
COPY requirements.txt /requirements-http.txt
RUN pip install --disable-pip-version-check --quiet --no-cache-dir -r /requirements-http.txt

# Setup http service as package
COPY . /code/http_service
# Use same version as bugbug
RUN python -c "import importlib.metadata; print(importlib.metadata.version('bugbug'))" > /code/http_service/VERSION
RUN pip install --disable-pip-version-check --no-cache-dir /code/http_service

# Run the Pulse listener in the background
CMD (bugbug-http-pulse-listener &) && gunicorn -b 0.0.0.0:$PORT bugbug_http.app --preload --timeout 30 -w 3
