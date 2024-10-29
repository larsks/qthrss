FROM python:3.12

WORKDIR /app
COPY Pipfile* ./
RUN pip install pipenv
RUN pipenv install
COPY ./ ./

CMD ["pipenv", "run", "gunicorn", "-b", "0.0.0.0", "--access-logfile", "/dev/stdout", "--error-logfile", "/dev/stderr", "wsgi:app"]
