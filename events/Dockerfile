FROM python:3.7-slim

ADD events /src

# Replace symlink by VERSION source file
RUN rm /src/VERSION
COPY VERSION /src/VERSION

RUN cd /src && pip install --disable-pip-version-check --quiet --no-cache-dir .

CMD ["bugbug-events"]
