FROM python:3.11-slim

Install system dependencies (ffmpeg for media processing)

RUN apt-get update && apt-get install -y --no-install-recommends 

ffmpeg 

&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

Copy requirements and install dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

Copy application files

COPY . .

Expose port and start FastAPI server

EXPOSE 10000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
