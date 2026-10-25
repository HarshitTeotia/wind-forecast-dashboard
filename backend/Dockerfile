FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data/wind_actuals.csv data/wind_forecasts.csv ./data/
RUN useradd --create-home --uid 10001 --user-group app \
    && chmod 0555 /app/data \
    && chmod 0444 /app/data/wind_actuals.csv /app/data/wind_forecasts.csv
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
