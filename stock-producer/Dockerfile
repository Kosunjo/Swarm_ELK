FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy producer script
COPY producer.py .

# Run the producer
CMD ["python", "producer.py"]