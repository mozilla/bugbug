ARG BUGBUG_VERSION=latest

FROM mozilla/bugbug-base:$BUGBUG_VERSION

COPY requirements.txt /code/http_service/

RUN pip install -r /code/http_service/requirements.txt

COPY . /code/http_service/

# Load the models
WORKDIR /code/

ARG CHECK_MODELS
ENV CHECK_MODELS="${CHECK_MODELS}"

RUN bash /code/http_service/ensure_models.sh

CMD python /code/http_service/worker.py high default low
