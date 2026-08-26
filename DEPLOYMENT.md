# Step-by-Step Deployment Guide: Executive Threat Advisory Agent

This guide provides an end-to-end, production-ready walkthrough for deploying the **Executive Threat Advisory Agent** (`exec_briefing_agent`) to **Google Cloud Vertex AI Agent Engine (Reasoning Engine)** and registering it with **Gemini Enterprise**.

---

## 🏛️ Architecture & Data Flow

```
[ User Prompt / Advisory URL ]
               │
               ▼
       ┌──────────────┐
       │  root_agent  │
       └──────┬───────┘
              │ delegates to
              ▼
    ┌──────────────────┐
    │ reporting_agent  │
    └─────────┬────────┘
              │ invokes
              ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  hunting_workflow (Sequential Agent)                         │
  │                                                              │
  │  1. keyword_extractor                                        │
  │     ↳ Scrapes bulletin URL & extracts CVEs / software keys   │
  │                   │                                          │
  │  2. ioc_collector                                            │
  │     ↳ Queries GTI MCP Server (/mcp) for threat IOCs & hashes │
  │                   │                                          │
  │  3. investigator                                             │
  │     ↳ Queries SecOps MCP Server (/mcp) for internal presence │
  │                   │                                          │
  │  4. consolidator                                             │
  │     ↳ Evaluates enterprise impact (Yes/No)                   │
  └───────────────────┬──────────────────────────────────────────┘
                      │
                      ▼
       [ 🛡️ EXECUTIVE THREAT ADVISORY ]
```

---

## 1. Prerequisites & Environment Setup

### 1.1 Requirements
- **Python**: `>= 3.10`
- **Google Cloud SDK (`gcloud`)**: Installed and authenticated
- **Permissions**:
  - Vertex AI Admin / User (`roles/aiplatform.user` or `roles/aiplatform.admin`)
  - Cloud Run Invoker (`roles/run.invoker`) on GTI and SecOps MCP services
  - Service Account Admin / IAM Policy Binding (`roles/iam.serviceAccountAdmin`, `roles/resourcemanager.projectIamAdmin`)

### 1.2 Installation & Authentication
```bash
# 1. Clone the repository & enter workspace
git clone https://github.com/yayefa/Executive-Briefing-Agent.git
cd Executive-Briefing-Agent

# 2. Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Authenticate with Google Cloud
gcloud auth login
gcloud auth application-default login
```

---

## 2. IAM & Service Account Configuration

The agent deployed to Vertex AI Agent Engine executes under a Google Cloud Service Account. It requires permissions to call Gemini models on Vertex AI and invoke the remote Cloud Run MCP servers.

```bash
# Set your environment variables
export PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export REGION="us-central1"
export SA_NAME="exec-briefing-agent-sa"
export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Set active gcloud project
gcloud config set project "$PROJECT_ID"

# 1. Create dedicated Service Account (if not already created)
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Executive Briefing Agent Service Account" \
  --project="$PROJECT_ID" || true

# 2. Grant Vertex AI User (to invoke Gemini models)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user"

# 3. Grant Cloud Run Invoker (to invoke GTI & SecOps MCP servers)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker"

# 4. Grant Service Account User (allows Vertex AI to run as this SA)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT_ID"
```

---

## 3. Local Verification Before Deployment

Always verify agent hierarchy, tool loading, and live MCP connectivity locally before deploying to the cloud:

```bash
# 1. Run unit test suite
python3 -m unittest discover -s tests

# 2. (Optional) Run interactive ADK Web UI for manual testing
adk web exec_briefing_agent
```

---

## 4. Deploying to Vertex AI Agent Engine

### Method A: Automated One-Command Deploy (Recommended)

The provided [`deploy.sh`](deploy.sh) script automatically:
1. Dynamically detects your active `PROJECT_ID`, `REGION`, and `PROJECT_NUMBER`.
2. Queries Cloud Run for `mcp-gti-mcp-server` and `mcp-secops-mcp-server` URLs.
3. Generates the deployment manifest (`.agent_engine_config.json`) and synchronizes `.env`.
4. Executes `adk deploy agent_engine`.

```bash
# Simple deploy (uses active gcloud project and defaults)
./deploy.sh
```

#### Custom Deploy Options & Overrides:
You can pass custom environment variables to `./deploy.sh`:

```bash
# Deploy with custom project, region, or service names
export PROJECT_ID="your-production-project"
export REGION="us-central1"
export GTI_SERVICE_NAME="mcp-gti-mcp-server"
export SECOPS_SERVICE_NAME="mcp-secops-mcp-server"
export SECOPS_AGENT_MODEL="gemini-2.5-flash"

./deploy.sh
```

#### In-Place Update (Preserve Existing Agent Engine ID):
To update an existing Reasoning Engine instance without creating a new resource ID:
```bash
export AGENT_ENGINE_ID="<YOUR_NUMERIC_REASONING_ENGINE_ID>"
./deploy.sh
```

---

### Method B: Manual ADK Deployment

If you prefer using the ADK CLI directly:

1. Copy `.env.example` to `exec_briefing_agent/.env` and update your settings:
   ```bash
   cp .env.example exec_briefing_agent/.env
   ```

2. Run the deployment command targeting `exec_briefing_agent`:
   ```bash
   adk deploy agent_engine \
     --project="<YOUR_GCP_PROJECT_ID>" \
     --region="us-central1" \
     --display_name="exec_briefing_agent" \
     exec_briefing_agent
   ```

---

## 5. Registering in Gemini Enterprise

Once deployed, register your Reasoning Engine instance with **Gemini Enterprise** (Discovery Engine / Agent Builder) so users can query the agent from enterprise chat applications.

### 5.1 Grant Access to the Gemini Enterprise Service Agent
The Gemini Enterprise discovery engine service agent must have permissions to query your Vertex AI Reasoning Engine:

```bash
PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
```

---

### 5.2 Register Agent in Google Cloud Console

1. Open the **[Google Cloud Console](https://console.cloud.google.com/)**.
2. Navigate to **Vertex AI** > **Agent Builder** > **Apps** (or **Gemini Enterprise**).
3. Select your Enterprise Chat Application.
4. In the left navigation menu, go to **Reasoning Engines** (or **Agents**) and click **+ Add Agent** / **+ Add Engine**.
5. Fill in the agent details:

| Field | Value |
| :--- | :--- |
| **Display Name** | `Executive Threat Advisory Agent` |
| **Description** | `Autonomous security intelligence agent that scrapes vulnerability notices/bulletins, queries Google Threat Intelligence (GTI) for IOCs and malware families, correlates findings against Google SecOps telemetry, and generates standardized CISO executive threat advisories.` |
| **Routing Prompt** | `Use this agent when the user provides a security bulletin, vulnerability notice, CVE identifier, or URL (e.g., MSRC, CISA, NIST NVD, vendor advisory) and requests an executive threat briefing, internal impact assessment, or security advisory.` |
| **Reasoning Engine Resource** | `projects/<PROJECT_NUMBER>/locations/<REGION>/reasoningEngines/<AGENT_ENGINE_ID>` |
| **Authentication** | `Google Cloud IAM / Service Account` (default) |

6. Click **Save & Publish**.

---

## 6. Verification & Sample Queries

In Gemini Enterprise Chat or the ADK Web UI, test the agent with any of the following prompts:

### Sample Queries:
- `"Analyze this security advisory and produce an executive briefing: https://msrc.microsoft.com/update-guide/vulnerability/CVE-2023-38831"`
- `"Please check if our enterprise is impacted by CVE-2024-3400 and generate a CISO threat advisory."`
- `"Provide an executive threat briefing on the latest CISA advisory for Palo Alto PAN-OS vulnerability."`

---

## 7. Troubleshooting & FAQ

### Q1: `401 Unauthorized` or `Empty Authorization header value` when calling MCP server
- **Cause**: Cloud Run requires an OIDC ID token bearing the exact audience URL (`https://<service-name>-<project-number>.<region>.run.app`).
- **Solution**: 
  - Locally: Run `gcloud auth application-default login`. The agent's `utils.py` automatically exchanges ADC refresh tokens for valid ID tokens.
  - On Vertex AI: Ensure the Reasoning Engine Service Account has `roles/run.invoker` on the MCP Cloud Run services.

### Q2: `TypeError: 'NoneType' object is not callable` in Starlette / FastMCP
- **Cause**: FastMCP expects SSE sessions on `GET /mcp` and messages on `POST /mcp/messages/?session_id=...`. Direct raw POSTs to `/mcp` without an active SSE session return `None` in FastMCP.
- **Solution**: The agent utilizes `SseServerParams` with normalized paths (`/mcp`) and automatic session lifecycle handling.

### Q3: How do I change the underlying Gemini model?
- Set the `SECOPS_AGENT_MODEL` environment variable (e.g., `export SECOPS_AGENT_MODEL="gemini-2.5-pro"` or `"gemini-2.5-flash"`). Default is `gemini-2.5-flash`.

