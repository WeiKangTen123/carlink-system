FROM python:3.11-slim

WORKDIR /app

# Install Linux system dependencies for Playwright Chromium PDF compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg curl unzip git build-essential \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["python", "-m", "app.main"]
