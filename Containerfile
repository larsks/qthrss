FROM python:3.12

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY ./ ./

CMD ["gunicorn", "-b", "0.0.0.0", "--access-logfile", "/dev/stdout", "--error-logfile", "/dev/stderr", "wsgi:app"]
