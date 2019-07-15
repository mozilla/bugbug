ARG BUGBUG_VERSION=latest

FROM mozilla/bugbug-base:$BUGBUG_VERSION

COPY requirements.txt /code/http_service/

RUN pip install -r /code/http_service/requirements.txt

COPY . /code/http_service/

# Load the models
WORKDIR /code/

CMD gunicorn -b 0.0.0.0:$PORT http_service.app --preload --timeout 30 -w 3
