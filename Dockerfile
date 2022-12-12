FROM python:3.11-slim-buster

EXPOSE 8000

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY check_httpd.py .

CMD ["python3", "check_httpd.py"]
