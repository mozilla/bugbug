FROM mozilla/bugbug-http-service-base:latest

# Load the models
WORKDIR /code/

COPY ./models /code/models

RUN python /code/bugbug_http_service/check_models.py

CMD ["gunicorn", "-b", "0.0.0.0:8000", "bugbug_http_service.app", "--preload", "--timeout", "30", "-w", "3"]
