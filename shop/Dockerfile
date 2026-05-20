FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_PATH=/app/shop.db
ENV LOG_PATH=/app/shop.jsonl

EXPOSE 5000

CMD ["python", "app.py"]
