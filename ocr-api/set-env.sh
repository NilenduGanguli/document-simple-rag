#!/bin/sh
set -e

# In a production environment, this script could be extended to fetch secrets 
# dynamically from Azure Key Vault, AWS Secrets Manager, or HashiCorp Vault 
# before starting the application.

echo "Exporting environment variables for OCR Service..."

# OCR Service Configuration
export PORT="8001"
export HOST="0.0.0.0"
export OPENAI_API_KEY="${OPENAI_API_KEY}"

#make key from .env file if exists
if [ -f .env ]; then
  echo "Loading environment variables from .env file..."
  for line in $(grep -v '^#' .env); do
    #split the line on '=' to get key and value
    key=$(echo $line | cut -d '=' -f 1)
    value=$(echo $line | cut -d '=' -f 2-)
    export "$key=$value"
  done
  # export $(grep -v '^#' .env | xargs)
fi

# Execute the main container command (CMD)
exec "$@"
