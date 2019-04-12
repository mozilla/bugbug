FROM mozilla/bugbug-base:latest

COPY requirements.txt /code/bugbug_http_service/

RUN pip install -r /code/bugbug_http_service/requirements.txt

COPY . /code/bugbug_http_service/

# Load the models
WORKDIR /code/

COPY models/ /code/models/

RUN python bugbug_http_service/models.py check

CMD ["gunicorn", "-b", "0.0.0.0:8000", "bugbug_http_service.app", "--preload", "--timeout", "30", "-w", "3"]