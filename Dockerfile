# Use official Python runtime as base image
FROM python:3.11-slim

# Install system dependencies (FFmpeg, Node.js, and build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    gnupg \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy python dependency list
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files to app directory
COPY . .

# Run the bot when the container starts
CMD ["python", "bot.py"]
