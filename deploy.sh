#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Executive Threat Advisory Agent - Automated Vertex AI Deployment
# ==============================================================================

export GOOGLE_API_USE_CLIENT_CERTIFICATE=false
export GOOGLE_API_USE_MTLS_ENDPOINT=never

# Read existing .env if present
if [[ -f "exec_briefing_agent/.env" ]]; then
  # export variables from .env if unset
  set -a
  source exec_briefing_agent/.env
  set +a
elif [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

# 1. Dynamically resolve GCP Project ID
PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "❌ Error: GCP Project ID not found. Please set PROJECT_ID or GOOGLE_CLOUD_PROJECT, or run 'gcloud config set project <PROJECT_ID>'." >&2
  exit 1
fi

# 2. Dynamically resolve GCP Region
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-${CLOUDSDK_COMPUTE_REGION:-$(gcloud config get-value compute/region 2>/dev/null || echo "us-central1")}}}"
if [[ -z "${REGION}" ]]; then
  REGION="us-central1"
fi

# 3. Dynamic Configuration Parameters
DISPLAY_NAME="${DISPLAY_NAME:-exec_briefing_agent}"
SECOPS_AGENT_MODEL="${SECOPS_AGENT_MODEL:-${MODEL_NAME:-gemini-2.5-flash}}"
SA_NAME="${SA_NAME:-exec-briefing-agent-sa}"
GTI_SERVICE_NAME="${GTI_SERVICE_NAME:-mcp-gti-mcp-server}"
SECOPS_SERVICE_NAME="${SECOPS_SERVICE_NAME:-mcp-secops-mcp-server}"
AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-}"

# 4. Dynamically query project number if not provided
if [[ -z "${PROJECT_NUMBER:-}" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)" 2>/dev/null || echo "")"
  if [[ -z "${PROJECT_NUMBER}" && "${PROJECT_ID}" == "gemini-entreprise-494918" ]]; then
    PROJECT_NUMBER="593610429940"
  fi
fi

# 5. Dynamically discover MCP URLs if not explicitly provided
if [[ -z "${GTI_MCP_URL:-}" ]]; then
  GTI_DISCOVERED="$(gcloud run services describe "${GTI_SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || echo "")"
  if [[ -n "${GTI_DISCOVERED}" ]]; then
    GTI_MCP_URL="${GTI_DISCOVERED%/}/mcp"
  elif [[ -n "${PROJECT_NUMBER}" ]]; then
    GTI_MCP_URL="https://${GTI_SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app/mcp"
  else
    GTI_MCP_URL=""
  fi
fi

if [[ -z "${SECOPS_MCP_URL:-}" ]]; then
  SECOPS_DISCOVERED="$(gcloud run services describe "${SECOPS_SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || echo "")"
  if [[ -n "${SECOPS_DISCOVERED}" ]]; then
    SECOPS_MCP_URL="${SECOPS_DISCOVERED%/}/mcp"
  elif [[ -n "${PROJECT_NUMBER}" ]]; then
    SECOPS_MCP_URL="https://${SECOPS_SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app/mcp"
  else
    SECOPS_MCP_URL=""
  fi
fi

echo "============================================================"
echo " 🚀 Deploying Executive Threat Advisory Agent to Agent Engine"
echo "============================================================"
echo " Project:          ${PROJECT_ID}"
echo " Region:           ${REGION}"
echo " Display Name:     ${DISPLAY_NAME}"
echo " Model:            ${SECOPS_AGENT_MODEL}"
echo " GTI Service:      ${GTI_SERVICE_NAME}"
echo " SecOps Service:   ${SECOPS_SERVICE_NAME}"
echo " GTI MCP URL:      ${GTI_MCP_URL:-'(dynamic resolution)'}"
echo " SecOps MCP URL:   ${SECOPS_MCP_URL:-'(dynamic resolution)'}"
if [[ -n "${AGENT_ENGINE_ID}" ]]; then
  echo " Agent Engine ID:  ${AGENT_ENGINE_ID} (In-Place Update)"
else
  echo " Agent Engine ID:  (New Deployment)"
fi
echo "============================================================"

# Dynamically construct .agent_engine_config.json and .env using python to ensure strict JSON validity and NO empty values
export PROJECT_ID REGION DISPLAY_NAME SECOPS_AGENT_MODEL GTI_SERVICE_NAME SECOPS_SERVICE_NAME GTI_MCP_URL SECOPS_MCP_URL PROJECT_NUMBER SA_NAME
python3 -c "
import json, os

raw_env_vars = {
    'GOOGLE_GENAI_USE_VERTEXAI': 'true',
    'GOOGLE_CLOUD_PROJECT': os.getenv('PROJECT_ID', ''),
    'GOOGLE_CLOUD_LOCATION': os.getenv('REGION', 'us-central1'),
    'GOOGLE_API_USE_CLIENT_CERTIFICATE': 'false',
    'SECOPS_AGENT_MODEL': os.getenv('SECOPS_AGENT_MODEL', 'gemini-2.5-flash'),
    'DISPLAY_NAME': os.getenv('DISPLAY_NAME', 'exec_briefing_agent'),
    'SA_NAME': os.getenv('SA_NAME', 'exec-briefing-agent-sa'),
    'GTI_SERVICE_NAME': os.getenv('GTI_SERVICE_NAME', 'mcp-gti-mcp-server'),
    'SECOPS_SERVICE_NAME': os.getenv('SECOPS_SERVICE_NAME', 'mcp-secops-mcp-server'),
    'GTI_MCP_URL': os.getenv('GTI_MCP_URL', ''),
    'SECOPS_MCP_URL': os.getenv('SECOPS_MCP_URL', ''),
    'PROJECT_NUMBER': os.getenv('PROJECT_NUMBER', ''),
    'GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY': 'true',
    'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT': 'true',
    'ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS': 'true',
}

# Strictly filter out any empty strings so Vertex AI Agent Engine never receives empty env values
clean_env_vars = {k: v for k, v in raw_env_vars.items() if v and str(v).strip() != ''}

config = {
    'display_name': os.getenv('DISPLAY_NAME', 'exec_briefing_agent'),
    'env_vars': clean_env_vars
}

with open('exec_briefing_agent/.agent_engine_config.json', 'w') as f:
    json.dump(config, f, indent=4)

with open('exec_briefing_agent/.env', 'w') as f:
    for k, v in clean_env_vars.items():
        f.write(f'{k}={v}\n')
"

# Copy configs to root for consistency
cp exec_briefing_agent/.agent_engine_config.json agent_engine_config.json
cp exec_briefing_agent/.env .env

echo "✅ Generated and synchronized deployment configuration and .env."

echo "📦 Initiating ADK deployment..."

DEPLOY_ARGS=(
  deploy agent_engine
  --project="${PROJECT_ID}"
  --region="${REGION}"
  --display_name="${DISPLAY_NAME}"
)

if [[ -n "${AGENT_ENGINE_ID}" ]]; then
  DEPLOY_ARGS+=(--agent_engine_id="${AGENT_ENGINE_ID}")
fi

DEPLOY_ARGS+=(exec_briefing_agent)

adk "${DEPLOY_ARGS[@]}"

echo "🎉 Deployment completed successfully!"
