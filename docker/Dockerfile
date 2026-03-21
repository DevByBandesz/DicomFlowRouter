FROM python:3.12-slim

# Metadata
LABEL maintainer="DevByBandesz"
LABEL version="1.0"
LABEL description="DICOM Flow Router with Retry Logic"

WORKDIR /app

# Install dependencies from the docker folder
COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source scripts from the src folder
COPY src/storescp_router.py .
COPY src/retry_sender.py .

# Setup working directory for DICOM queuing
RUN mkdir -p /app/Working
VOLUME ["/app/Working"]

# Default DICOM port for the router
EXPOSE 11112

# Start the Router by default
CMD ["python", "storescp_router.py", "--working-dir", "/app/Working"]