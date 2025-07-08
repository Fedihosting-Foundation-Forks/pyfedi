# syntax=docker/dockerfile:1.4
FROM --platform=$BUILDPLATFORM python:3-alpine AS builder


RUN adduser -D python

RUN apk add --no-cache pkgconfig gcc python3-dev musl-dev tesseract-ocr tesseract-ocr-data-eng postgresql-client bash

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=source=requirements.txt,target=/tmp/requirements.txt \
    pip3 install -r /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install gunicorn

# dd integration, should be before copying files to allow layer caching/reuse
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install ddtrace

RUN python -c "import compileall; compileall.compile_path()"

COPY --chown=python:python . /app

WORKDIR /app

USER python
RUN python -m compileall /app

RUN pybabel compile -d app/translations || true

RUN chmod u+x ./entrypoint.sh
RUN chmod u+x ./entrypoint_celery.sh

ENTRYPOINT ["./entrypoint.sh"]

# keep dd integration customization below this to minimize conflicts if possible

ARG DD_VERSION=dev
ARG DD_GIT_REPOSITORY_URL=unknown
ARG DD_GIT_COMMIT_SHA=unknown

ENV DD_SERVICE piefed
ENV DD_VERSION ${DD_VERSION}
ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL}
ENV DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}
ENV DD_LOGS_INJECTION=true
