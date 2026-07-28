FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Default: run MCP server
CMD ["python", "-m", "services.server"]

# Expose MCP server port (if HTTP transport is used)
EXPOSE 8000
