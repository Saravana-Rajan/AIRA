FROM python:3.12-slim
WORKDIR /app
COPY server.py .
ENV PYTHONUNBUFFERED=1
USER nobody
CMD ["python", "server.py"]
