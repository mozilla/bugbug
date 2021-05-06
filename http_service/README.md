### Local development

**For starting the service locally run the following commands.**

Start Redis:

    docker-compose up redis

Build the http service image:

    docker build -t mozilla/bugbug-http-service -f Dockerfile .

Start the http service:

    docker-compose up bugbug-http-service

Build the background worker image:

    docker build -t mozilla/bugbug-http-service-bg-worker --build-arg TAG=latest -f Dockerfile.bg_worker .

Run the background worker:

    docker-compose up bugbug-http-service-bg-worker
