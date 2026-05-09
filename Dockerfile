FROM python:3.11-slim

# تحديث النظام وتثبيت Google Chrome الرسمي والمكاتب الأساسية
RUN apt-get update && apt-get install -y wget gnupg unzip \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# إعداد مساحة العمل
WORKDIR /app

# نسخ ملفات المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الكود
COPY . .

# تشغيل السيرفر
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]