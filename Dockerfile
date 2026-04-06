# Official Playwright image already has all Chromium dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (deps already satisfied by base image)
RUN playwright install chromium

# Copy app files
COPY . .

# Expose port
EXPOSE 10000

# Start with single worker
CMD ["gunicorn", "app:app", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:10000"]