FROM mozilla/bugbug-base:latest

RUN env

COPY requirements.txt /code/bugbug_http_service/

RUN pip install -r /code/bugbug_http_service/requirements.txt

COPY . /code/bugbug_http_service/

# Load the models
WORKDIR /code/

COPY ./models /code/models

ARG CHECK_MODELS
ENV CHECK_MODELS="${CHECK_MODELS}"

RUN bash /code/bugbug_http_service/ensure_models.sh

CMD gunicorn -b 0.0.0.0:$PORT bugbug_http_service.app --preload --timeout 30 -w 3
