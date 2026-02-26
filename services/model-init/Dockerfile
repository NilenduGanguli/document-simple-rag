FROM python:3.12-slim

WORKDIR /app

# Install build tools
RUN apt-get update && apt-get install -y gcc g++ && rm -rf /var/lib/apt/lists/*

# Install shared package
COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

# Install service dependencies (includes torch for ONNX export)
COPY services/model-init/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/model-init/model_init.py .

CMD ["python", "model_init.py"]
