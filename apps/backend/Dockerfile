FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/backend/requirements.txt /app/apps/backend/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /app/apps/backend/requirements.txt

COPY apps/backend /app/apps/backend
COPY configs /app/configs

WORKDIR /app/apps/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
