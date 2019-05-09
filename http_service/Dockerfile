FROM mozilla/bugbug-base:latest

RUN env

COPY requirements.txt /code/bugbug_http_service/

RUN pip install -r /code/bugbug_http_service/requirements.txt

COPY . /code/bugbug_http_service/

# Load the models
WORKDIR /code/

ARG CHECK_MODELS
ENV CHECK_MODELS="${CHECK_MODELS}"

RUN python /code/bugbug_http_service/check_models.py

CMD ["gunicorn", "-b", "0.0.0.0:8000", "bugbug_http_service.app", "--preload", "--timeout", "30", "-w", "3"]
