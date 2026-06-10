FROM python:3.12-alpine

# Build + runtime packages (openssh-client is required for the S2 ssh ProxyCommand).
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev openssh-client

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
